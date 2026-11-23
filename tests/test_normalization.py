from book_normalization import normalize_main_title, normalize_publication_year
from publisher_normalization import normalize_publisher, pub_jaccard, pub_tokens


def test_main_title_removes_known_trailing_additions_but_keeps_other_colons():
    assert normalize_main_title("A Title (novel)") == "A Title"
    assert normalize_main_title("A Title (movie tie-in)") == "A Title"
    assert normalize_main_title("A Title (Part 2): The Sequel") == "A Title (Part 2): The Sequel"
    assert normalize_main_title("A Title: A Novel") == "A Title"
    assert normalize_main_title("A Title: A Memoir (series note)") == "A Title"
    assert normalize_main_title("The Lost Fleet: Dauntless") == "The Lost Fleet: Dauntless"


def test_dbpedia_secondary_title_cleanup_is_source_scoped():
    value = "Death in Paradise (novel)  Death in Paradise  1"
    assert normalize_main_title(value, source="dbpedia") == "Death in Paradise"
    assert normalize_main_title(value, source="amazon") == value


def test_publication_year_preserves_missing_and_rejects_unrecognized_values():
    assert normalize_publication_year("2003") == "2003"
    assert normalize_publication_year("2003-05-14") == "2003"
    assert normalize_publication_year("May 14, 2003") == "2003"
    assert normalize_publication_year("05/14/2003") == "2003"
    assert normalize_publication_year("0779-03-21") == "0779"
    assert normalize_publication_year("2003-19-44") is None
    assert normalize_publication_year("unknown") is None
    assert normalize_publication_year(None) is None


def test_publisher_normalization_retains_distinguishing_terms_and_handles_missing():
    assert normalize_publisher("Hill and Wang") == normalize_publisher("Hill & Wang")
    assert "press" in pub_tokens("Penguin Press")
    assert "books" not in pub_tokens("Penguin Books")
    assert pub_tokens(None) == set()
    assert pub_jaccard(None, None) == 0.0
    assert pub_jaccard("Penguin Books", "Penguin Press") < 1.0
    assert pub_jaccard("Penguin Group (USA)", "Penguin Group (UK)") < 1.0
