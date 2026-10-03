"""Text normalization and stable hashing rules."""

from hashlib import sha256

from web_monitor.normalization import normalize_html


def test_presentation_only_changes_and_whitespace_do_not_change_hash():
    old = normalize_html('<title> A title </title><p class="old">Hello &amp; world</p><script>old()</script><style>p{color:red}</style><!-- old -->')
    new = normalize_html('<title>A title</title><p id="new"> Hello  &amp;\n world </p><script>new()</script><style>p{color:blue}</style><template>hidden</template><!-- new -->')
    assert old.text == new.text == "A title Hello & world"
    assert old.title == new.title == "A title"
    assert old.content_hash == new.content_hash == sha256(old.text.encode("utf-8")).hexdigest()


def test_meaningful_text_and_title_changes_affect_hash():
    old = normalize_html("<title>News</title><p>Monday</p>")
    assert old.content_hash != normalize_html("<title>News</title><p>Tuesday</p>").content_hash
    assert old.content_hash != normalize_html("<title>Update</title><p>Monday</p>").content_hash


def test_missing_title_empty_text_and_links():
    page = normalize_html('<a href="/z"></a><a href="/a"></a><a href="/z"></a>')
    assert page.title is None
    assert page.text == ""
    assert page.links == ("/a", "/z")


def test_unicode_content_is_preserved():
    page = normalize_html('<meta charset="utf-8"><p>Café 日本</p>'.encode())
    assert page.text == "Café 日本"
