if __name__ == "__main__":
    """
    test_excerpt_search.py
    ======================
    Regression tests for evidence excerpt search
    (``batch02_ra_sourcing.mask_non_prose`` / ``find_excerpt_candidates`` /
    ``find_excerpt``).

    Why these tests exist
    ---------------------
    BCSGK-0359-B (বিরিশিরি is in দুর্গাপুর upazila, নেত্রকোণা district) was logged
    as `insufficient -- শুধু image caption-এ মিলেছে` and sent back for re-sourcing.
    The article's own first sentence says exactly what the claim says. The search
    never reached it: the window that held both terms first was the infobox, where
    `image_skyline = বিরিশিরির উপছবি.jpg` sits 100 characters from
    `image_caption = সুসং দুর্গাপুরের ...`, and the old code returned the first hit.

    A filename beside a caption is two nearby strings, not an assertion. The cost
    of getting this wrong runs both ways: it hid real evidence here, and on
    another row the same window would have been taken in as support.

    TEST 1 is that specific regression.

        python test_excerpt_search.py
    """

    from __future__ import annotations

    import sys

    import batch02_ra_sourcing as B

    CHECKS: list[bool] = []


    def check(label: str, condition: bool, detail: str = "") -> None:
        CHECKS.append(bool(condition))
        print("  [%s] %s%s" % ("PASS" if condition else "FAIL", label,
                               (" -- %s" % detail) if detail else ""))


    # A cut-down copy of the real bn.wikipedia বিরিশিরি revision: an infobox whose
    # filename and caption hold both search terms, then the lead sentence.
    BIRISHIRI = """{{Infobox settlement
    | official_name = বিরিশিরি
    | image_skyline = বিরিশিরির উপছবি.jpg
    | image_caption = সুসং দুর্গাপুরের চিনামাটির পাহাড়
    | population_total =
    }}

    '''বিরিশিরি''' [[নেত্রকোণা জেলা|নেত্রকোণা]] জেলার \
    [[দুর্গাপুর উপজেলা, নেত্রকোণা|দুর্গাপুর উপজেলার]] ऐতিহ্যবাহী একটি গ্রাম।
    """

    MUST, ANY_OF = ["বিরিশিরি"], ["দুর্গাপুর", "নেত্রকোণা"]


    # ---------------------------------------------------------------------------
    print("TEST 1: REGRESSION -- an infobox hit never outranks the lead sentence")
    # ---------------------------------------------------------------------------
    cands = B.find_excerpt_candidates(BIRISHIRI, MUST, ANY_OF)
    check("the infobox window is found but classed markup_only",
          any(c["region"] == "markup_only" and "image_caption" in c["excerpt"]
              for c in cands))
    check("at least one prose candidate exists",
          any(c["region"] == "prose" for c in cands))
    check("prose candidates are ordered ahead of markup",
          [c["region"] for c in cands] == sorted(
              [c["region"] for c in cands], key=lambda r: r != "prose"))

    excerpt = B.find_excerpt(BIRISHIRI, MUST, ANY_OF)
    check("find_excerpt returns the lead sentence",
          excerpt is not None and "ऐতিহ্যবাহী" in excerpt, (excerpt or "")[:70])
    check("the returned excerpt carries no infobox markup",
          excerpt is not None and "image_caption" not in excerpt
          and "Infobox" not in excerpt)


    # ---------------------------------------------------------------------------
    print("TEST 2: a markup-only hit is never returned as evidence")
    # ---------------------------------------------------------------------------
    # Same infobox, lead sentence deleted: there is no prose support at all, and
    # the honest answer is None rather than the caption.
    caption_only = BIRISHIRI.split("'''")[0]
    check("a caption-only article yields no excerpt",
          B.find_excerpt(caption_only, MUST, ANY_OF) is None)
    check("but the rejected lead is still reported",
          any(c["region"] == "markup_only"
              for c in B.find_excerpt_candidates(caption_only, MUST, ANY_OF)))


    # ---------------------------------------------------------------------------
    print("TEST 3: both terms have to survive masking")
    # ---------------------------------------------------------------------------
    # Subject in prose, corroborating term only in a caption. Reversing the order
    # of the false lead must not smuggle it back in.
    split_terms = ("{{Infobox settlement | image_caption = সুসং দুর্গাপুরের পাহাড় }}\n\n"
                    "'''বিরিশিরি''' একটি গ্রাম।")
    check("prose subject + caption-only object does not count as prose",
          B.find_excerpt(split_terms, MUST, ["দুর্গাপুর"]) is None)


    # ---------------------------------------------------------------------------
    print("TEST 4: mask_non_prose keeps offsets usable")
    # ---------------------------------------------------------------------------
    masked = B.mask_non_prose(BIRISHIRI)
    check("masking preserves length, so offsets still line up",
          len(masked) == len(BIRISHIRI),
          "%d vs %d" % (len(masked), len(BIRISHIRI)))
    check("the infobox is blanked", "image_caption" not in masked)
    check("the lead sentence survives", "ऐতিহ্যবাহী" in masked)

    nested = "a {{outer | x = {{inner | y = দুর্গাপুর }} }} b"
    nested_masked = B.mask_non_prose(nested)
    check("an inner template's content is masked too",
          "দুর্গাপুর" not in nested_masked, repr(nested_masked))
    check("the prose either side of a nested template is kept",
          "a" in nested_masked and "b" in nested_masked)

    ref_text = "গ্রামটি ভালো।<ref name=\"x\">দুর্গাপুর থেকে লেখা।</ref> শেষ।"
    check("<ref> bodies are masked",
          "দুর্গাপুর" not in B.mask_non_prose(ref_text))

    category = "একটি গ্রাম। [[বিষয়শ্রেণী:নেত্রকোণা জেলা]]"
    check("category tags are masked",
          "নেত্রকোণা" not in B.mask_non_prose(category))

    # Regression: the pilot batch returned `bd/dhaka-metro-rail/ Dhaka Metro Rail:
    # At A Glance] at Travel Mate Bangladesh` as support for BCSGK-0087. That is a
    # citation label inside a bare external link, not a sentence in the article.
    extlink = ("মেট্রোরেল। [http://example.com/dhaka-metro-rail/ Dhaka Metro Rail: "
               "At A Glance] at Travel Mate Bangladesh")
    extlink_masked = B.mask_non_prose(extlink)
    # Only what is inside the brackets is the citation label. The words after the
    # closing bracket are ordinary article text and must survive -- masking them
    # would be over-reach, and the test should say which is which.
    check("the label inside a bare external link is masked",
          "At A Glance" not in extlink_masked and "example.com" not in extlink_masked,
          repr(extlink_masked))
    check("text outside the brackets is left alone",
          "Travel Mate" in extlink_masked)
    check("prose before an external link survives",
          "মেট্রোরেল" in extlink_masked)


    # ---------------------------------------------------------------------------
    print("TEST 5: excerpts are cut at sentence boundaries")
    # ---------------------------------------------------------------------------
    check("the excerpt ends on a danda",
          excerpt is not None and excerpt.rstrip().endswith("।"),
          (excerpt or "")[-40:])
    check("the excerpt does not start mid-template",
          excerpt is not None and not excerpt.lstrip().startswith("|"))


    # ---------------------------------------------------------------------------
    print()
    passed = sum(CHECKS)
    print("test_excerpt_search: %d/%d CHECK True -- %s"
          % (passed, len(CHECKS), "PASS" if passed == len(CHECKS) else "FAIL"))
    sys.exit(0 if passed == len(CHECKS) else 1)
