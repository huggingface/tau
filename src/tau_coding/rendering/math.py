"""Terminal-friendly rendering for a small, explicit LaTeX math subset.

The renderer is intentionally conservative: unsupported LaTeX macros and
malformed spans are returned with their original delimiters so assistant output
does not get partially mangled.
"""

import re
from collections.abc import Mapping

_MACRO_PATTERN = re.compile(r"\\([A-Za-z]+)")
_CURRENCY_PREFIX_PATTERN = re.compile(r"\d+(?:[.,]\d+)*\b")
_MATH_SIGNAL_CHARS = frozenset("\\^_{}=+-*/<>")

_GREEK_SYMBOLS: Mapping[str, str] = {
    "alpha": "α",
    "beta": "β",
    "gamma": "γ",
    "delta": "δ",
    "epsilon": "ε",
    "varepsilon": "ϵ",
    "zeta": "ζ",
    "eta": "η",
    "theta": "θ",
    "vartheta": "ϑ",
    "iota": "ι",
    "kappa": "κ",
    "lambda": "λ",
    "mu": "μ",
    "nu": "ν",
    "xi": "ξ",
    "omicron": "ο",
    "pi": "π",
    "rho": "ρ",
    "sigma": "σ",
    "tau": "τ",
    "upsilon": "υ",
    "phi": "φ",
    "varphi": "ϕ",
    "chi": "χ",
    "psi": "ψ",
    "omega": "ω",
    "Gamma": "Γ",
    "Delta": "Δ",
    "Theta": "Θ",
    "Lambda": "Λ",
    "Xi": "Ξ",
    "Pi": "Π",
    "Sigma": "Σ",
    "Upsilon": "Υ",
    "Phi": "Φ",
    "Psi": "Ψ",
    "Omega": "Ω",
}

_OPERATOR_SYMBOLS: Mapping[str, str] = {
    "times": "×",
    "cdot": "·",
    "leq": "≤",
    "geq": "≥",
    "neq": "≠",
    "pm": "±",
    "approx": "≈",
    "rightarrow": "→",
    "infty": "∞",
    "sum": "∑",
    "int": "∫",
    "in": "∈",
    "forall": "∀",
    "exists": "∃",
}

_SUPPORTED_MACROS: Mapping[str, str] = {
    **_GREEK_SYMBOLS,
    **_OPERATOR_SYMBOLS,
}

_SUPERSCRIPT: Mapping[str, str] = {
    "0": "⁰",
    "1": "¹",
    "2": "²",
    "3": "³",
    "4": "⁴",
    "5": "⁵",
    "6": "⁶",
    "7": "⁷",
    "8": "⁸",
    "9": "⁹",
    "+": "⁺",
    "-": "⁻",
    "=": "⁼",
    "(": "⁽",
    ")": "⁾",
    "a": "ᵃ",
    "b": "ᵇ",
    "c": "ᶜ",
    "d": "ᵈ",
    "e": "ᵉ",
    "f": "ᶠ",
    "g": "ᵍ",
    "h": "ʰ",
    "i": "ⁱ",
    "j": "ʲ",
    "k": "ᵏ",
    "l": "ˡ",
    "m": "ᵐ",
    "n": "ⁿ",
    "o": "ᵒ",
    "p": "ᵖ",
    "r": "ʳ",
    "s": "ˢ",
    "t": "ᵗ",
    "u": "ᵘ",
    "v": "ᵛ",
    "w": "ʷ",
    "x": "ˣ",
    "y": "ʸ",
    "z": "ᶻ",
    "A": "ᴬ",
    "B": "ᴮ",
    "D": "ᴰ",
    "E": "ᴱ",
    "G": "ᴳ",
    "H": "ᴴ",
    "I": "ᴵ",
    "J": "ᴶ",
    "K": "ᴷ",
    "L": "ᴸ",
    "M": "ᴹ",
    "N": "ᴺ",
    "O": "ᴼ",
    "P": "ᴾ",
    "R": "ᴿ",
    "T": "ᵀ",
    "U": "ᵁ",
    "V": "ⱽ",
    "W": "ᵂ",
}

_SUBSCRIPT: Mapping[str, str] = {
    "0": "₀",
    "1": "₁",
    "2": "₂",
    "3": "₃",
    "4": "₄",
    "5": "₅",
    "6": "₆",
    "7": "₇",
    "8": "₈",
    "9": "₉",
    "+": "₊",
    "-": "₋",
    "=": "₌",
    "(": "₍",
    ")": "₎",
    "a": "ₐ",
    "e": "ₑ",
    "h": "ₕ",
    "i": "ᵢ",
    "j": "ⱼ",
    "k": "ₖ",
    "l": "ₗ",
    "m": "ₘ",
    "n": "ₙ",
    "o": "ₒ",
    "p": "ₚ",
    "r": "ᵣ",
    "s": "ₛ",
    "t": "ₜ",
    "u": "ᵤ",
    "v": "ᵥ",
    "x": "ₓ",
}


def render_terminal_math(text: str) -> str:
    """Render supported ``$...$`` and ``$$...$$`` math spans as Unicode text.

    Supported LaTeX is deliberately bounded to common Greek names, the fixed
    operator table above, and ``^`` / ``_`` scripts using either one character or
    a brace group. Spans with unknown macros, unbalanced delimiters, or likely
    currency amounts are left unchanged.
    """
    if "$" not in text:
        return text

    block_parts = _render_block_spans(text)
    return "".join(
        part if protected else _render_inline_spans(part) for part, protected in block_parts
    )


def _render_block_spans(text: str) -> list[tuple[str, bool]]:
    parts: list[tuple[str, bool]] = []
    index = 0

    while index < len(text):
        start = _find_unescaped(text, "$$", index)
        if start is None:
            parts.append((text[index:], False))
            break

        end = _find_unescaped(text, "$$", start + 2)
        if end is None:
            if start > index:
                parts.append((text[index:start], False))
            parts.append((text[start:], True))
            break

        if start > index:
            parts.append((text[index:start], False))

        inner = text[start + 2 : end]
        raw = text[start : end + 2]
        rendered = _render_math_span(inner)
        parts.append((rendered if rendered is not None else raw, True))
        index = end + 2

    return parts


def _render_inline_spans(text: str) -> str:
    parts: list[str] = []
    index = 0

    while index < len(text):
        start = _find_inline_dollar(text, index)
        if start is None:
            parts.append(text[index:])
            break

        end = _find_inline_dollar(text, start + 1)
        if end is None:
            parts.append(text[index:])
            break

        parts.append(text[index:start])
        inner = text[start + 1 : end]
        raw = text[start : end + 1]
        parts.append(_render_math_span(inner) or raw)
        index = end + 1

    return "".join(parts)


def _find_inline_dollar(text: str, start: int) -> int | None:
    index = text.find("$", start)
    while index != -1:
        previous_is_dollar = index > 0 and text[index - 1] == "$"
        next_is_dollar = index + 1 < len(text) and text[index + 1] == "$"
        if not previous_is_dollar and not next_is_dollar and not _is_escaped(text, index):
            return index
        index = text.find("$", index + 1)
    return None


def _find_unescaped(text: str, delimiter: str, start: int) -> int | None:
    index = text.find(delimiter, start)
    while index != -1:
        if not _is_escaped(text, index):
            return index
        index = text.find(delimiter, index + len(delimiter))
    return None


def _is_escaped(text: str, index: int) -> bool:
    backslashes = 0
    cursor = index - 1
    while cursor >= 0 and text[cursor] == "\\":
        backslashes += 1
        cursor -= 1
    return backslashes % 2 == 1


def _render_math_span(expression: str) -> str | None:
    if not _looks_like_math(expression):
        return None
    if _contains_unknown_macro(expression):
        return None

    rendered = _MACRO_PATTERN.sub(lambda match: _SUPPORTED_MACROS[match.group(1)], expression)
    if "\\" in rendered:
        return None

    scripted = _render_scripts(rendered)
    if scripted is None:
        return None
    return scripted.replace("{", "").replace("}", "")


def _looks_like_math(expression: str) -> bool:
    stripped = expression.strip()
    if not stripped:
        return False

    has_math_signal = any(character in _MATH_SIGNAL_CHARS for character in stripped)
    if _CURRENCY_PREFIX_PATTERN.match(stripped) and not has_math_signal:
        return False
    return not (any(character.isspace() for character in stripped) and not has_math_signal)


def _contains_unknown_macro(expression: str) -> bool:
    return any(
        match.group(1) not in _SUPPORTED_MACROS for match in _MACRO_PATTERN.finditer(expression)
    )


def _render_scripts(expression: str) -> str | None:
    rendered: list[str] = []
    index = 0

    while index < len(expression):
        character = expression[index]
        if character not in {"^", "_"}:
            rendered.append(character)
            index += 1
            continue

        script, next_index = _read_script(expression, index + 1)
        if script is None:
            return None
        script_map = _SUPERSCRIPT if character == "^" else _SUBSCRIPT
        rendered.append(_translate_script(script, script_map))
        index = next_index

    return "".join(rendered)


def _read_script(expression: str, index: int) -> tuple[str, int] | tuple[None, int]:
    if index >= len(expression) or expression[index].isspace():
        return None, index

    if expression[index] != "{":
        return expression[index], index + 1

    depth = 1
    cursor = index + 1
    while cursor < len(expression):
        character = expression[cursor]
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return expression[index + 1 : cursor], cursor + 1
        cursor += 1
    return None, index


def _translate_script(script: str, mapping: Mapping[str, str]) -> str:
    return "".join(mapping.get(character, character) for character in script)
