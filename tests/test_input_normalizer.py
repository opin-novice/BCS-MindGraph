from bcs.pipeline.input_normalizer import InputNormalizer, BANGLA_DIGIT_MAP


def test_normalize_bangla_digits():
    n = InputNormalizer()
    result = n.normalize("৪৫৬")
    assert "৪" not in result.normalized_text
    assert "456" in result.normalized_text


def test_normalize_nfc():
    n = InputNormalizer()
    composed = "আমার"
    result = n.normalize(composed)
    assert result.normalized_text


def test_script_detection_bangla():
    n = InputNormalizer()
    result = n.normalize("বাংলাদেশের রাজধানী ঢাকা")
    assert result.script_type == "bangla"


def test_script_detection_english():
    n = InputNormalizer()
    result = n.normalize("Dhaka is the capital")
    assert result.script_type == "english"


def test_script_detection_mixed():
    n = InputNormalizer()
    result = n.normalize("বাংলাদেশের capital ঢাকা")
    assert result.script_type == "mixed"


def test_bangla_digit_map_all():
    expected = "0123456789"
    actual = "".join(BANGLA_DIGIT_MAP[d] for d in "০১২৩৪৫৬৭৮৯")
    assert actual == expected


def test_empty_input():
    n = InputNormalizer()
    result = n.normalize("")
    assert result.normalized_text == ""
    assert result.script_type == "unknown"


def test_whitespace_collapse():
    n = InputNormalizer()
    result = n.normalize("বাংলা    দেশের   রাজধানী")
    assert "  " not in result.normalized_text
