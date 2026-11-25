"""Translate the supported Python 3 subset into native Bend 2 source."""
import ast

from .numeric import Compiler as PureCompiler
from .effects import EffectCompiler, uses_effects
from .syntax import TranslationError

__all__ = ['Compiler', 'TranslationError', 'translate']


class Compiler:
    """Dispatch to the pure or explicit-IO lowering backend."""

    def __init__(self, source: str, source_name: str = '<input>', *,
                 show_return: bool = False, threshold: int = 8):
        tree = ast.parse(source, filename=source_name)
        if uses_effects(tree):
            self.backend = EffectCompiler(tree, source_name, show_return=show_return)
        else:
            self.backend = PureCompiler(source, source_name, show_return=show_return,
                                        threshold=threshold)

    def compile(self) -> str:
        return self.backend.compile()


def translate(source: str, filename: str = '<input>', *, show_return: bool = False,
              gc_threshold: int = 8) -> str:
    """Emit Bend source; successful emission does not certify native execution."""
    return Compiler(source, filename, show_return=show_return,
                    threshold=gc_threshold).compile()
