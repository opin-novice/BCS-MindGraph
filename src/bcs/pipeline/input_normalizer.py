"""
input_normalizer.py
===================
Step 1: Input & Normalization
BCSBatighor GK Knowledge Graph System

Role: Pipeline Step 1 (no single owner — shared utility)

Responsibilities
----------------
1. Accept raw Bangla (or mixed Bangla-English) question text.
2. Normalize Unicode representations (NFC).
3. Normalize Bangla digits → ASCII digits.
4. Collapse whitespace and strip noise.
5. Detect script type (Bangla / English / Mixed).
6. Return a structured NormalizedInput object ready for Step 2.

No external dependencies — pure Python stdlib only.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Bangla Unicode block
# ---------------------------------------------------------------------------
BANGLA_START = 0x0980
BANGLA_END   = 0x09FF

# Bangla digit → ASCII digit mapping
BANGLA_DIGIT_MAP: Dict[str, str] = {
    '০': '0', '১': '1', '২': '2', '৩': '3', '৪': '4',
    '৫': '5', '৬': '6', '৭': '7', '৮': '8', '৯': '9',
}

# Bangla punctuation normalizations
BANGLA_PUNCTUATION_MAP: Dict[str, str] = {
    '।': '.',   # daari → full stop
    '॥': '.',   # double daari → full stop
    '\u200c': '',  # ZWNJ — remove
    '\u200d': '',  # ZWJ  — remove
    '\u200b': '',  # Zero-width space — remove
    '\u00a0': ' ', # Non-breaking space → regular space
    '\u2013': '-', # En dash → hyphen
    '\u2014': '-', # Em dash → hyphen
    '\u201c': '"', # Left double quote
    '\u201d': '"', # Right double quote
    '\u2018': "'", # Left single quote
    '\u2019': "'", # Right single quote
}

# Filler words / discourse markers that carry no factual weight
BANGLA_FILLER_WORDS = {
    'আচ্ছা', 'হ্যালো', 'ভাই', 'বন্ধু', 'দোস্ত',
    'please', 'plz', 'pls', 'bro', 'dude',
}

# Common question markers in Bangla
BANGLA_QUESTION_MARKERS = {
    'কি', 'কী', 'কে', 'কার', 'কাকে', 'কোন', 'কোথায়',
    'কখন', 'কবে', 'কেন', 'কীভাবে', 'কতটুকু', 'কত',
    'কতটি', 'কতজন', 'কীসের', 'কীসে',
}


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass
class NormalizedInput:
    """
    Result of normalizing a raw user question.

    Attributes
    ----------
    raw_text : str
        The original, unmodified input.
    normalized_text : str
        Cleaned, normalized text ready for intent extraction.
    script_type : str
        'bangla', 'english', or 'mixed'.
    detected_language : str
        ISO code — 'bn' for Bangla, 'en' for English, 'mixed'.
    has_question_marker : bool
        True if a Bangla or English question word was found.
    digit_normalized : bool
        True if any Bangla digit was converted to ASCII.
    tokens : list of str
        Whitespace-split tokens of the normalized text.
    word_count : int
        Number of tokens.
    warnings : list of str
        Any normalization issues detected.
    """
    raw_text:            str
    normalized_text:     str
    script_type:         str
    detected_language:   str
    has_question_marker: bool
    digit_normalized:    bool
    tokens:              List[str] = field(default_factory=list)
    word_count:          int = 0
    warnings:            List[str] = field(default_factory=list)

    def __str__(self):
        return (
            f"NormalizedInput(\n"
            f"  raw        = \"{self.raw_text[:60]}\"\n"
            f"  normalized = \"{self.normalized_text[:60]}\"\n"
            f"  script     = {self.script_type}\n"
            f"  lang       = {self.detected_language}\n"
            f"  q_marker   = {self.has_question_marker}\n"
            f"  words      = {self.word_count}\n"
            f"  warnings   = {self.warnings}\n"
            f")"
        )


# ---------------------------------------------------------------------------
# InputNormalizer
# ---------------------------------------------------------------------------

class InputNormalizer:
    """
    Step 1 — Input & Normalization component.

    Converts a raw Bangla (or mixed) question into a clean, structured
    NormalizedInput object that the Intent/Blueprint Builder (Step 2)
    can consume directly.

    Usage
    -----
    normalizer = InputNormalizer()
    result = normalizer.normalize("বাংলাদেশের রাজধানী কী?")
    print(result.normalized_text)   # "বাংলাদেশের রাজধানী কী?"
    print(result.detected_language) # "bn"
    """

    def __init__(self, preserve_bangla_daari: bool = False):
        """
        Parameters
        ----------
        preserve_bangla_daari : bool
            If True, '।' is kept as-is instead of converting to '.'.
            Useful when downstream text processing prefers Bangla punctuation.
        """
        self.preserve_bangla_daari = preserve_bangla_daari
        self._punct_map = dict(BANGLA_PUNCTUATION_MAP)
        if preserve_bangla_daari:
            self._punct_map.pop('।', None)
            self._punct_map.pop('॥', None)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def normalize(self, text: str) -> NormalizedInput:
        """
        Normalize a raw input string.

        Parameters
        ----------
        text : str
            Raw user question (Bangla, English, or mixed).

        Returns
        -------
        NormalizedInput
        """
        if not text or not text.strip():
            return NormalizedInput(
                raw_text=text or "",
                normalized_text="",
                script_type="unknown",
                detected_language="unknown",
                has_question_marker=False,
                digit_normalized=False,
                tokens=[],
                word_count=0,
                warnings=["Empty or whitespace-only input."],
            )

        raw = text
        warnings: List[str] = []

        # Step 1a: Unicode NFC normalization
        normalized = unicodedata.normalize("NFC", text)

        # Step 1b: Strip invisible/control characters
        normalized = self._strip_control_chars(normalized)

        # Step 1c: Apply punctuation map
        normalized = self._apply_punct_map(normalized)

        # Step 1d: Normalize Bangla digits → ASCII
        digit_normalized = False
        new_text = []
        for ch in normalized:
            if ch in BANGLA_DIGIT_MAP:
                new_text.append(BANGLA_DIGIT_MAP[ch])
                digit_normalized = True
            else:
                new_text.append(ch)
        normalized = "".join(new_text)

        # Step 1e: Collapse whitespace
        normalized = re.sub(r'\s+', ' ', normalized).strip()

        # Step 1f: Remove filler words at start/end
        normalized, filler_removed = self._remove_fillers(normalized)
        if filler_removed:
            warnings.append(f"Removed filler word(s): {filler_removed}")

        # Step 1g: Ensure sentence ends with a question mark or period
        normalized = self._ensure_terminal_punctuation(normalized)

        # Detect script & language
        script_type         = self._detect_script(normalized)
        detected_language   = self._detect_language(script_type)

        # Detect question marker
        has_q_marker = self._has_question_marker(normalized)

        # Warnings
        if len(normalized) < 5:
            warnings.append("Very short input — intent extraction may be unreliable.")
        if not has_q_marker:
            warnings.append("No question marker found — treating as implicit question.")

        tokens     = normalized.split()
        word_count = len(tokens)

        return NormalizedInput(
            raw_text=raw,
            normalized_text=normalized,
            script_type=script_type,
            detected_language=detected_language,
            has_question_marker=has_q_marker,
            digit_normalized=digit_normalized,
            tokens=tokens,
            word_count=word_count,
            warnings=warnings,
        )

    def normalize_batch(self, texts: List[str]) -> List[NormalizedInput]:
        """
        Normalize a list of raw input texts.

        Parameters
        ----------
        texts : list of str

        Returns
        -------
        list of NormalizedInput
        """
        return [self.normalize(t) for t in texts]

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _strip_control_chars(self, text: str) -> str:
        """Remove ASCII control characters (except tab/newline → space)."""
        result = []
        for ch in text:
            cat = unicodedata.category(ch)
            if cat.startswith('C') and ch not in '\t\n\r':
                continue  # skip control chars
            if ch in '\t\n\r':
                result.append(' ')
            else:
                result.append(ch)
        return "".join(result)

    def _apply_punct_map(self, text: str) -> str:
        """Apply Bangla punctuation normalization map."""
        for src, tgt in self._punct_map.items():
            text = text.replace(src, tgt)
        return text

    def _remove_fillers(self, text: str) -> Tuple[str, List[str]]:
        """
        Remove known filler words from the beginning/end of the text.

        Returns (cleaned_text, list_of_removed_fillers).
        """
        tokens  = text.split()
        removed = []
        # Remove from the front
        while tokens and tokens[0].rstrip('.,!?') in BANGLA_FILLER_WORDS:
            removed.append(tokens.pop(0))
        # Remove from the back
        while tokens and tokens[-1].rstrip('.,!?') in BANGLA_FILLER_WORDS:
            removed.append(tokens.pop())
        return " ".join(tokens), removed

    def _ensure_terminal_punctuation(self, text: str) -> str:
        """Add '?' if no terminal punctuation is present."""
        if not text:
            return text
        if text[-1] not in '.?!।':
            return text + '?'
        return text

    def _detect_script(self, text: str) -> str:
        """
        Detect the dominant script in the text.

        Returns 'bangla', 'english', or 'mixed'.
        """
        bangla_count  = 0
        latin_count   = 0
        for ch in text:
            cp = ord(ch)
            if BANGLA_START <= cp <= BANGLA_END:
                bangla_count += 1
            elif ch.isalpha() and cp < 128:
                latin_count += 1

        total = bangla_count + latin_count
        if total == 0:
            return 'unknown'
        bangla_ratio = bangla_count / total
        if bangla_ratio >= 0.80:
            return 'bangla'
        elif bangla_ratio <= 0.20:
            return 'english'
        else:
            return 'mixed'

    def _detect_language(self, script_type: str) -> str:
        """Map script type to ISO language code."""
        return {'bangla': 'bn', 'english': 'en', 'mixed': 'mixed'}.get(
            script_type, 'unknown'
        )

    def _has_question_marker(self, text: str) -> bool:
        """Check for Bangla or English question markers or terminal '?'."""
        if '?' in text:
            return True
        lower = text.lower()
        # English WH-words
        for marker in ('what', 'who', 'when', 'where', 'why', 'how', 'which'):
            if re.search(r'\b' + marker + r'\b', lower):
                return True
        # Bangla question words
        for marker in BANGLA_QUESTION_MARKERS:
            if marker in text:
                return True
        return False


# ---------------------------------------------------------------------------
# Convenience wrapper (for pipeline.py)
# ---------------------------------------------------------------------------

def normalize_input(raw_text: str, preserve_bangla_daari: bool = False) -> NormalizedInput:
    """
    Module-level convenience function.

    Parameters
    ----------
    raw_text : str
        Raw user question.
    preserve_bangla_daari : bool
        Keep '।' as-is if True.

    Returns
    -------
    NormalizedInput
    """
    return InputNormalizer(preserve_bangla_daari=preserve_bangla_daari).normalize(raw_text)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    normalizer = InputNormalizer()

    test_cases = [
        "বাংলাদেশের রাজধানী কী?",
        "ভাই, বাংলাদেশের প্রথম রাষ্ট্রপতি কে?",
        "পদ্মা সেতুর দৈর্ঘ্য কত কিলোমিটার",
        "Bangladesh achieved independence in which year?",
        "বাংলাদেশের মুক্তিযুদ্ধ ১৯৭১ সালে কবে শুরু হয়?",
        "মুজিবনগর সরকার গঠিত হয় কোন তারিখে?",
        "Who is the father of the Bengali nation?",
        "   ",   # Empty / whitespace
        "Sundarbans কোথায় অবস্থিত?",
        "বাংলাদেশের মোট জেলা কয়টি?",
    ]

    print("\n" + "=" * 65)
    print("  input_normalizer.py — Self Test")
    print("=" * 65 + "\n")

    for i, tc in enumerate(test_cases, 1):
        result = normalizer.normalize(tc)
        print(f"[{i:02d}] Input      : {repr(tc[:50])}")
        print(f"      Normalized : {repr(result.normalized_text[:60])}")
        print(f"      Script     : {result.script_type}  |  Lang: {result.detected_language}")
        print(f"      Q-Marker   : {result.has_question_marker}  |  Words: {result.word_count}")
        if result.warnings:
            print(f"      Warnings   : {result.warnings}")
        print()

    print("  Self-test complete.\n")
