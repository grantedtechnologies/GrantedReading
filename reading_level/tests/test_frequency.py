from reading_level.word_frequency import FrequencyTable, OOV_LOGFREQ


def test_common_words_score_higher_than_rare_words(table):
    assert table.logfreq("the") > table.logfreq("dog")
    assert table.logfreq("dog") > table.logfreq("proliferation")


def test_lookup_is_case_insensitive(table):
    assert table.logfreq("Dog") == table.logfreq("dog")


def test_unknown_words_return_the_default_not_zero(table):
    logfreq = table.logfreq("zzzqqxnotaword")
    assert logfreq == OOV_LOGFREQ
    assert logfreq != 0.0


def test_proper_nouns_are_flagged(table):
    proper = [
        word
        for word in ("michael", "jennifer", "david", "sarah")
        if table.is_proper(word)
    ]
    assert proper, "expected at least one common first name to be flagged proper"
    assert not table.is_proper("dog")


def test_percentile_orders_words_by_rarity(table):
    assert table.percentile("the") > table.percentile("proliferation")
    assert 0.0 <= table.percentile("dog") <= 100.0


def test_table_is_not_loaded_at_construction(tmp_path):
    # Loading at import or construction time would slow Flask startup and
    # break test collection.
    missing = FrequencyTable(tmp_path / "absent.json.gz")
    assert missing._entries is None


def test_meta_records_provenance(table):
    meta = table.meta
    assert meta["entry_count"] > 0
    assert meta["source"]
