"""Regression for Qwen latent marker registration under Transformers 4.56 and 5.

The Intel HDB smoke failed before model loading at
  AssertionError: Key extra_special_tokens is not a special token
because v4 exposed that attribute without accepting it in add_special_tokens.
These doubles enforce the public contract without downloading model weights.
"""
import pytest

from tools import nanojev_qwen_hybridagent as hybrid


class AddedToken:
    def __init__(self, content, normalized=False, special=True):
        self.content = content
        self.normalized = normalized
        self.special = special

    def __str__(self):
        return self.content


class Tokenizer:
    SPECIAL_TOKENS_ATTRIBUTES = ()

    def __init__(self):
        self.ids = {'<old>': 151934}
        self.registration_keys = []

    def add_special_tokens(self, tokens):
        key, entries = next(iter(tokens.items()))
        if key not in self.SPECIAL_TOKENS_ATTRIBUTES:
            raise AssertionError('Key ' + key + ' is not a special token')
        self.registration_keys.append(key)
        for entry in entries:
            word = str(entry)
            if word not in self.ids:
                self.ids[word] = max(self.ids.values()) + 1
        setattr(self, key, entries)

    def convert_tokens_to_ids(self, token):
        return self.ids[token]


class Transformers456(Tokenizer):
    SPECIAL_TOKENS_ATTRIBUTES = ('bos_token', 'additional_special_tokens', 'eos_token')

    def __init__(self):
        super().__init__()
        self.old = AddedToken('<old>', normalized=False, special=True)
        self.additional_special_tokens = [self.old]
        # The bug: v4 ALSO exposes this attribute although it is not a key
        # accepted by its add_special_tokens() implementation.
        self.extra_special_tokens = {'legacy alias': self.old}


class Transformers5(Tokenizer):
    SPECIAL_TOKENS_ATTRIBUTES = ('bos_token', 'extra_special_tokens', 'eos_token')

    def __init__(self):
        super().__init__()
        self.old = AddedToken('<old>', normalized=False, special=True)
        self.extra_special_tokens = [self.old]


class Transformers5Extended(Transformers5):
    # v5 can add extra_special_tokens to the accepted list inside its method.
    SPECIAL_TOKENS_ATTRIBUTES = ('bos_token', 'eos_token')

    def add_special_tokens(self, tokens):
        original = self.SPECIAL_TOKENS_ATTRIBUTES
        self.SPECIAL_TOKENS_ATTRIBUTES = (*original, 'extra_special_tokens')
        try:
            return super().add_special_tokens(tokens)
        finally:
            self.SPECIAL_TOKENS_ATTRIBUTES = original


@pytest.mark.parametrize('cls, key', [
    (Transformers456, 'additional_special_tokens'),
    (Transformers5, 'extra_special_tokens'),
    (Transformers5Extended, 'extra_special_tokens'),
])
def test_markers_are_registered_atomically_with_preserved_ids_and_metadata(cls, key):
    tok = cls()
    first = hybrid._register_latent_markers(tok, AddedToken)
    assert tok.registration_keys == [key]
    entries = getattr(tok, key)
    assert entries[0] is tok.old
    assert [str(t) for t in entries] == ['<old>', hybrid.START, hybrid.END]
    assert all(t.special and not t.normalized for t in entries)
    assert tok.ids['<old>'] == 151934
    assert first == (151935, 151936)
    assert hybrid._register_latent_markers(tok, AddedToken) == first
    assert len(getattr(tok, key)) == 3
    assert tok.ids['<old>'] == 151934


def test_unsupported_tokenizer_refused_without_registration():
    with pytest.raises(RuntimeError, match='HYBRID_TOKENIZER_NO_SUPPORTED_SPECIAL_TOKEN_LIST'):
        hybrid._register_latent_markers(Tokenizer(), AddedToken)
