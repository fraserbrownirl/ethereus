"""Packing keeps a read of the store equal to the file: pieces end at line ends, and the store adds each one's newline."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
import pack_skills as P  # noqa: E402


def enc(s):
    return s.encode()  # one token per byte is enough to test the cutting


def read(pieces):
    return "".join(bytes(p).decode() + "\n" for p in pieces)


def test_a_read_is_exactly_the_file_when_lines_fit():
    text = "# Head\n\nline one\nline two\n\n\nlast\n"
    for limit in (9, 12, 30, 200):
        pieces = P.split_pieces(text, enc, limit, fill=0)
        assert read(pieces) == text and all(len(p) <= limit for p in pieces)


def test_a_long_line_breaks_only_where_a_space_was():
    line = "First sentence here. Second one follows on. Third goes on a little longer than the rest."
    text = f"top\n{line}\nend\n"
    pieces = P.split_pieces(text, enc, 30)
    assert all(len(p) <= 30 for p in pieces)
    assert read(pieces).replace("\n", " ") == text.replace("\n", " ")
    assert "0\n." not in read(pieces)


def test_no_break_inside_a_number_or_a_word():
    text = "stETH fell to 0.93 ETH in June and every word of this stays whole across the pieces\n"
    for limit in (12, 20, 33):
        for tok in read(P.split_pieces(text, enc, limit)).split():
            assert tok in text.split()


def test_small_pieces_fill_up_and_words_stay_whole():
    words = " ".join(f"word{i}" for i in range(40))
    text = "\n".join(words for _ in range(6)) + "\n"
    pieces = P.split_pieces(text, enc, 120, 0.8)
    assert all(len(p) <= 120 for p in pieces)
    assert read(pieces).replace("\n", " ").split() == text.split()
    assert sum(len(p) for p in pieces) / len(pieces) > 0.8 * 120


def test_a_line_without_spaces_still_packs_when_it_fits():
    text = "a" * 20 + "\n" + "b" * 20 + "\n"
    pieces = P.split_pieces(text, enc, 25, 0.8)
    assert read(pieces) == text
