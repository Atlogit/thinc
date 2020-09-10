from typing import Tuple, Callable, Optional, cast

from ..model import Model
from ..config import registry
from ..types import Array3d, Array2d, Floats3d


InT = Array3d


@registry.layers("with_reshape.v1")
def with_reshape(layer: Model[Array2d, Array2d]) -> Model[InT, InT]:
    """Reshape data on the way into and out from a layer."""
    return Model(
        f"with_reshape({layer.name})",
        forward,
        init=init,
        layers=[layer],
        dims={"nO": None, "nI": None},
    )


def forward(model: Model[InT, InT], X: InT, is_train: bool) -> Tuple[InT, Callable]:
    layer = model.layers[0]
    initial_shape = X.shape
    final_shape = list(initial_shape[:-1]) + [layer.get_dim("nO")]
    nB = X.shape[0]
    nT = X.shape[1]
    X2d = cast(InT, model.ops.reshape(X, (-1, X.shape[2])))
    Y2d, Y2d_backprop = layer(X2d, is_train=is_train)
    Y = model.ops.reshape3f(Y2d, *final_shape)

    def backprop(dY: InT) -> InT:
        dY_floats = model.ops.asarray3f(cast(Floats3d, dY))
        reshaped = model.ops.reshape2f(dY_floats, nB * nT, -1)
        return Y2d_backprop(model.ops.reshape3f(reshaped, *initial_shape))

    return Y, backprop


def init(
    model: Model[InT, InT], X: Optional[Array3d] = None, Y: Optional[Array3d] = None
) -> Model[InT, InT]:
    layer = model.layers[0]
    if X is None and Y is None:
        layer.initialize()
        return model
    X2d: Optional[Array2d] = None
    Y2d: Optional[Array2d] = None
    if X is not None:
        X2d = cast(Array2d, model.ops.reshape(X, (-1, X.shape[-1])))
    if Y is not None:
        Y2d = cast(Array2d, model.ops.reshape(Y, (-1, Y.shape[-1])))
    layer.initialize(X=X2d, Y=Y2d)
    if layer.has_dim("nI"):
        model.set_dim("nI", layer.get_dim("nI"))
    if layer.has_dim("nO"):
        model.set_dim("nO", layer.get_dim("nO"))
    return model
