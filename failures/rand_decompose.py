import torch
from torch.utils._python_dispatch import TorchDispatchMode
import functorch
from functorch.compile import aot_function, aot_module, draw_graph, print_compile
import torch.utils.checkpoint


def print_rng_seed_and_offset():
    state = torch.cuda.get_rng_state()
    seed = state[800:808].view(dtype=torch.int64)
    offset = state[808:].view(dtype=torch.int64)
    print(f"seed={seed}, offset={offset}", flush=True)


def test_custom_object():

    shape = (4,)
    class Custom(torch.autograd.Function):
        @staticmethod
        def forward(ctx, x):
            state = torch.cuda.get_rng_state()
            ctx.save_for_backward(x, state)
            # a = torch.rand_like(x) * torch.rand_like(x)
            # a = torch.rand(16, 32, 48, device="cuda") * torch.rand(48, device="cuda") * torch.sin(x)
            a = torch.rand(*shape, device="cuda") * torch.rand(*shape, device="cuda") * torch.sin(x)
            torch.cuda.set_rng_state(state)
            # a = torch.rand_like(x) * torch.rand_like(x) * a
            # a = torch.rand(16, 32, 48, device="cuda") * torch.rand(16, 32, 48, device="cuda") * a
            a = torch.rand(*shape, device="cuda") * torch.rand(*shape, device="cuda") * a
            return a

        @staticmethod
        def backward(ctx, grad_out):
            x, state = ctx.saved_tensors
            torch.cuda.set_rng_state(state)
            return grad_out * torch.rand_like(grad_out) * torch.cos(x)



    custom = Custom.apply

    # x = torch.rand(16, 32, 48, device="cuda", requires_grad=True)
    x = torch.rand(*shape, device="cuda", requires_grad=True)
    aot_custom = aot_function(custom, print_compile)

    # Both forward
    loss = aot_custom(x).sum()
    torch.manual_seed(16)
    loss.backward()


def test_rst_state_in_between():
    def fn(x):
        state = torch.cuda.get_rng_state()
        x = torch.sin(x)
        x = x + torch.rand(4, device="cuda")
        torch.cuda.set_rng_state(state)
        x = x + torch.rand(4, device="cuda")
        return x

    x = torch.randn(4, device="cuda")

    aot_mod = aot_function(fn, print_compile)
    aot_mod(x)


def test_negative_testing():
    torch.manual_seed(16)
    bad_state = torch.cuda.get_rng_state()
    def fn(x):
        torch.manual_seed(32)
        x = torch.sin(x)
        x = x + torch.rand(4, device="cuda")
        torch.cuda.set_rng_state(bad_state)
        x = x + torch.rand(4, device="cuda")
        return x

    x = torch.randn(4, device="cuda")

    aot_mod = aot_function(fn, print_compile)
    try:
        aot_mod(x)
        assert False
    except NotImplementedError:
        pass


def test_checkpointing():

    @torch._dynamo.allow_in_graph
    class MockModule(torch.nn.Module):
        def __init__(self):
            super().__init__()

        def forward_impl_(self, x):
            a = torch.rand(4, device="cuda") + torch.sin(x)
            a = torch.rand(4, 4, device="cuda").sum(axis=0) + torch.sin(a)
            a = torch.rand(4, device="cuda") + torch.sin(a)
            a = torch.nn.functional.dropout(a)
            return a

        def forward(self, x):
            return torch.utils.checkpoint.checkpoint(self.forward_impl_, x, use_reentrant=False, preserve_rng_state=True)

    mod = MockModule()


    def fn(x):
        a = torch.sigmoid(x)
        a = mod(x)
        return a

    x = torch.randn(4, device="cuda", requires_grad=True)

    aot_mod = aot_function(fn, print_compile)
    print_rng_seed_and_offset()
    aot_mod(x).sum().backward()

    for _ in range(16):
        print_rng_seed_and_offset()
        aot_mod(x).sum().backward()
    # opt_mod = torch.compile(fn, backend="aot_eager_decomp_partition")
    # opt_mod(x).sum().backward()

if __name__ == "__main__":
    test_custom_object()
    # test_rst_state_in_between()
    # test_negative_testing()
    # test_checkpointing()