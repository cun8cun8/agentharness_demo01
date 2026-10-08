from src.csv_counter import count_data_rows


def test_ignores_header_and_blank_lines():
    csv_text = "name,score\nAda,10\n\nGrace,9\n"
    assert count_data_rows(csv_text) == 2


def test_empty_csv_has_zero_rows():
    assert count_data_rows("name,score\n") == 0

