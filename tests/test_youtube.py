import pytest

from yti_cliente.youtube import UrlNoAdmitida, extraer_video_id, resolver_ref_video, url_corta

ID = "dQw4w9WgXcQ"


@pytest.mark.parametrize("url", [
    f"https://www.youtube.com/watch?v={ID}&t=10",
    f"https://youtu.be/{ID}?si=x",
    f"http://m.youtube.com/watch?feature=share&v={ID}",
    f"https://music.youtube.com/watch?v={ID}",
    f"https://YOUTUBE.com/shorts/{ID}",
    f"https://www.youtube.com/embed/{ID}",
    f"https://www.youtube.com/live/{ID}",
])
def test_extrae_id(url):
    assert extraer_video_id(url) == ID


@pytest.mark.parametrize("url", [
    "https://vimeo.com/123",
    f"ftp://youtu.be/{ID}",
    f"https://user@youtu.be/{ID}",
    "https://www.youtube.com/watch?v=corto",
    "https://www.youtube.com/channel/UCx",
    "no es url",
    "",
    f"https://youtube.com.evil.io/watch?v={ID}",
])
def test_rechaza(url):
    with pytest.raises(UrlNoAdmitida):
        extraer_video_id(url)


def test_url_corta():
    assert url_corta(ID) == f"https://youtu.be/{ID}"


def test_resolver_ref_video_url_e_id():
    assert resolver_ref_video("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert resolver_ref_video("  dQw4w9WgXcQ ") == "dQw4w9WgXcQ"
    assert resolver_ref_video("a-b_c123456") == "a-b_c123456"


@pytest.mark.parametrize("texto", ["", "corto", "dQw4w9WgXcQ12", "https://example.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXc!"])
def test_resolver_ref_video_invalido(texto):
    with pytest.raises(UrlNoAdmitida):
        resolver_ref_video(texto)
