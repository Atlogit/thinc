import numpy

try:
    import cupy
    import cupyx
    import cupy.cuda
    from cupy.cuda.compiler import compile_with_cache  # noqa: F401

    has_cupy = True

    # We no longer have to set up the memory pool, fortunately.
except ImportError:
    cupy = None
    cupyx = None
    has_cupy = False

if has_cupy:
    from cupy.cudnn import CUDNN_UNIDIRECTIONAL, CUDNN_BIDIRECTIONAL
    from cupy.cudnn import CUDNN_LSTM
else:
    CUDNN_UNIDIRECTIONAL = 0
    CUDNN_BIDIRECTIONAL = 1
    CUDNN_LSTM = 0


from .ops import Ops
from .numpy_ops import NumpyOps
from . import _custom_kernels
from ..types import DeviceTypes


class CupyOps(Ops):
    name = "cupy"
    xp = cupy
    _xp2 = cupyx

    def __init__(
        self, device_type: DeviceTypes = "gpu", device_id: int = 0, **kwargs
    ) -> None:
        self.device_type = device_type
        self.device_id = device_id

    def to_numpy(self, data):
        if isinstance(data, numpy.ndarray):
            return data
        else:
            return data.get()

    def gemm(self, x, y, out=None, trans1=False, trans2=False):
        if isinstance(x, numpy.ndarray) or isinstance(y, numpy.ndarray):
            raise ValueError(
                "Encountered a numpy array when processing with cupy. "
                "Did you call model.ops.asarray on your data?"
            )
        if trans1:
            x = x.T
        if trans2:
            y = y.T
        if out is None:
            return self.xp.dot(x, y)
        else:
            self.xp.dot(x, y, out=out)
            return out

    def asarray(self, data, dtype=None):
        # This is sort of frustrating, but we can't easily otherwise pass
        # forward "unset".
        dtype = {"dtype": dtype} if dtype is not None else {}
        if isinstance(data, cupy.ndarray):
            return self.xp.asarray(data, **dtype)
        elif hasattr(data, "data_ptr"):
            # Handles PyTorch Tensors
            pointer = cupy.cuda.MemoryPointer(data.data_ptr())
            shape = data.stride()
            array = self.xp.ndarray(shape, memptr=pointer, **dtype)
            return array
        else:
            result = self.xp.array(data, **dtype)
            return result

    def lstm_forward_training(
        self,
        params,
        H0,
        C0,
        X,
        size_at_t,
    ):
        dropout_state = cupy.cudnn.DropoutStates(None, 0)
        batch_size = lengths[0]
        num_layers, dirs, hidden_size = C0
 
        reserve_space, Hy, Cy, Y = cupy.cudnn.rnn_forward_training(
            dropout_state,
            CUDNN_UNIDIRECTIONAL if dirs != 2 else CUDNN_BIDIRECTIONAL,
            CUDNN_LSTM, 
            H0,
            C0,
            params,
            X,
            size_at_t
        )
        return Y, (reserve_space, dropout_state, Hy, Cy, X, Y, H0, C0)

    def lstm_forward_inference(
        self,
        params,
        H0,
        C0,
        X,
        size_at_t,
    ):
        Y, _ = self.lstm_forward_training(params, H0, C0, X, size_at_t)
        return Y

    def backprop_lstm(
        self, dY, lengths, params, fwd_state
    ):
        reserve_space, dropout_state, Hy, Cy, X, Y, H0, C0 = fwd_state
        batch_size = lengths[0]
        num_layers, dirs, hidden_size = C0
        dHy = self.alloc_3d(num_layers, batch_size, hidden_size)
        dCy = self.alloc_3d(num_layers, batch_size, hidden_size * 4)
        dhx, dcx, dxs = cupy.cudnn.rnn_backward_data(
            dropout_state,
            CUDNN_UNIDIRECTIONAL if dirs < 2 else CUDNN_BIDIRECTIONAL,
            CUDNN_LSTM,
            H0,
            C0,
            params,
            X,
            Y,
            reserve_space,
            dHy,
            dCy,
            dY,
            lengths
        )
        d_params = cupy.cudnn.rnn_backward_weights(
            states,
            CUDNN_UNIDIRECTIONAL if dirs < 2 else CUDNN_BIDIRECTIONAL,
            CUDNN_LSTM,
            X,
            H0,
            Y,
            params,
            reserve_space,
            lengths
        ) 
        return dX, d_params

    def maxout(self, X):
        return _custom_kernels.maxout(X)

    def backprop_maxout(self, dY, which, P):
        return _custom_kernels.backprop_maxout(dY, which, P)

    def relu(self, X, inplace=False):
        if not inplace:
            return X * (X > 0)
        else:
            X *= X > 0
            return X

    def backprop_relu(self, dY, Y, inplace=False):
        if not inplace:
            return dY * (Y > 0)
        dY *= Y > 0
        return dY

    def mish(self, X, threshold=20.0):
        return _custom_kernels.mish(X, threshold=threshold, out=None)

    def backprop_mish(self, dY, X, threshold=20.0, out=None):
        return _custom_kernels.backprop_mish(dY, X, threshold=threshold, out=out)

    def clip_gradient(self, gradient, threshold):
        grad_norm = cupy.maximum(cupy.linalg.norm(gradient), 1e-12)
        gradient *= cupy.minimum(threshold, grad_norm) / grad_norm
        return gradient

    def seq2col(self, seq, nW):
        """Given an (M, N) sequence of vectors, return an (M, N*(nW*2+1)) sequence.
        The new sequence is constructed by concatenating nW preceding and succeeding
        vectors onto each column in the sequence, to extract a window of features.
        """
        return _custom_kernels.seq2col(seq, nW)

    def backprop_seq2col(self, dY, nW):
        return _custom_kernels.backprop_seq2col(dY, nW)

    def reduce_mean(self, X, lengths):
        return _custom_kernels.reduce_mean(X, lengths)

    def backprop_reduce_mean(self, d_means, lengths):
        return _custom_kernels.backprop_reduce_mean(d_means, lengths)

    def reduce_max(self, X, lengths):
        return _custom_kernels.reduce_max(X, lengths)

    def backprop_reduce_max(self, d_maxes, which, lengths):
        return _custom_kernels.backprop_reduce_max(d_maxes, which, lengths)

    def reduce_sum(self, X, lengths):
        return _custom_kernels.reduce_sum(X, lengths)

    def backprop_reduce_sum(self, d_sums, lengths):
        return _custom_kernels.backprop_reduce_sum(d_sums, lengths)

    def hash(self, ids, seed):
        return _custom_kernels.hash(ids, seed)

    def scatter_add(self, table, indices, values):
        self._xp2.scatter_add(table, indices, values)

    def adam(
        self, weights, gradient, mom1, mom2, beta1, beta2, eps, learn_rate, mod_rate=1.0
    ):
        adam_kernel(
            gradient, learn_rate, 1 - beta1, 1 - beta2, eps, weights, mom1, mom2
        )
        gradient.fill(0)
        return weights, gradient, mom1, mom2

    def position_encode(self, N, D, period=10000, out=None):
        positions = NumpyOps().position_encode(N, D, period=period, out=out)
        return self.asarray(positions)


if cupy is not None:
    adam_kernel = cupy.ElementwiseKernel(
        "T grad, T lr, T one_minus_beta1, T one_minus_beta2, T eps",
        "T param, T m, T v",
        """m += one_minus_beta1 * (grad - m);
        v += one_minus_beta2 * (grad * grad - v);
        param -= lr * m / (sqrt(v) + eps);""",
        "adam",
    )
else:
    adam_kernel = None
