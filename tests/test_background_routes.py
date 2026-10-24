"""
Background pictures: upload, fetch, remove, and who may see one.

The pictures are personal, so most of what is asserted here is about the boundary:
a picture is served only to a logged-in user with a weft wearing it, and what is stored
is never the file that was sent — re-encoded, shrunk, and stripped of the metadata a
phone photo carries (its GPS position above all).

Images are built in the test with Pillow rather than kept as fixtures, so each test
shows exactly what it uploads.
"""

import io
import os
import time

import pytest
from PIL import Image

from app import backgrounds
from tests.test_auth_routes import PASSWORD, login, make_user
from tests.test_provider_routes import signed_in
from tests.test_weft_routes import add_weft


@pytest.fixture(autouse=True)
def picture_dir(tmp_path, monkeypatch):
    """Every test gets its own directory; none of them writes into data/."""
    path = tmp_path / "backgrounds"
    monkeypatch.setenv("WEFT_BACKGROUNDS_DIR", str(path))
    return path


def picture(size=(64, 48), colour=(200, 80, 40), fmt="PNG", mode="RGB", **save) -> bytes:
    img = Image.new(mode, size, colour)
    out = io.BytesIO()
    img.save(out, format=fmt, **save)
    return out.getvalue()


async def make_wefts(client, *names):
    """Wefts by name, unbound: a picture needs no engine. Returns {name: id}."""
    return {name: (await add_weft(client, name))["id"] for name in names}


async def weft_named(client, name):
    return next(w for w in (await client.get("/api/wefts")).json() if w["name"] == name)


async def upload(client, weft_id, data, content_type="image/png"):
    return await client.put(
        f"/api/wefts/{weft_id}/background",
        content=data,
        headers={"Content-Type": content_type},
    )


async def clear(client, weft_id):
    return await client.delete(f"/api/wefts/{weft_id}/background")


def stored(response_bytes: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(response_bytes))
    img.load()
    return img


# ---- the round trip ---------------------------------------------------------


@pytest.mark.asyncio
async def test_a_new_weft_has_no_picture(csrf_client, session):
    await signed_in(csrf_client, session)
    await make_wefts(csrf_client, "alpha-model")

    assert (await weft_named(csrf_client, "alpha-model"))["background"] is None


@pytest.mark.asyncio
async def test_upload_then_fetch(csrf_client, session, picture_dir):
    await signed_in(csrf_client, session)
    ids = await make_wefts(csrf_client, "alpha-model", "zeta-model")

    response = await upload(csrf_client, ids["alpha-model"], picture())
    assert response.status_code == 200
    digest = response.json()["background"]
    assert len(digest) == 64

    # The menu carries it, for this weft only.
    assert (await weft_named(csrf_client, "alpha-model"))["background"] == digest
    assert (await weft_named(csrf_client, "zeta-model"))["background"] is None

    fetched = await csrf_client.get(f"/api/wefts/background/{digest}")
    assert fetched.status_code == 200
    assert fetched.headers["content-type"] == "image/webp"
    assert "immutable" in fetched.headers["cache-control"]
    assert "private" in fetched.headers["cache-control"]
    img = stored(fetched.content)
    assert img.format == "WEBP"
    assert img.size == (64, 48)

    assert (picture_dir / f"{digest}.webp").is_file()


@pytest.mark.asyncio
async def test_the_weft_page_sees_the_picture(csrf_client, session):
    await signed_in(csrf_client, session)
    ids = await make_wefts(csrf_client, "alpha-model")
    digest = (await upload(csrf_client, ids["alpha-model"], picture())).json()["background"]

    weft = (await csrf_client.get(f"/api/wefts/{ids['alpha-model']}")).json()
    assert weft["background"] == digest


@pytest.mark.asyncio
async def test_the_same_picture_twice_is_one_file(csrf_client, session, picture_dir):
    await signed_in(csrf_client, session)
    ids = await make_wefts(csrf_client, "alpha-model", "zeta-model")

    a = (await upload(csrf_client, ids["alpha-model"], picture())).json()["background"]
    b = (await upload(csrf_client, ids["zeta-model"], picture())).json()["background"]
    assert a == b
    assert len(list(picture_dir.glob("*.webp"))) == 1


# ---- what is stored is not what was sent ------------------------------------


@pytest.mark.asyncio
async def test_a_large_picture_is_shrunk_to_the_long_edge(csrf_client, session):
    await signed_in(csrf_client, session)
    ids = await make_wefts(csrf_client, "alpha-model")

    digest = (
        await upload(csrf_client, ids["alpha-model"], picture(size=(4000, 1000)))
    ).json()["background"]

    img = stored((await csrf_client.get(f"/api/wefts/background/{digest}")).content)
    assert img.size == (backgrounds.LONG_EDGE, backgrounds.LONG_EDGE // 4)


@pytest.mark.asyncio
async def test_a_phone_photo_loses_its_location_and_stands_up(csrf_client, session):
    """The two things a phone photo carries that matter here: where it was taken, and
    which way up it was held. The first must not survive; the second must be applied."""
    await signed_in(csrf_client, session)
    ids = await make_wefts(csrf_client, "alpha-model")

    exif = Image.Exif()
    exif[0x0112] = 6  # Orientation: rotate 90° clockwise to display
    exif[0x8825] = {2: (34.0, 55.0, 0.0), 4: (138.0, 36.0, 0.0)}  # GPSInfo
    jpeg = picture(size=(80, 40), fmt="JPEG", exif=exif.tobytes())
    # The fixture really does carry both, or this test passes for the boring reason.
    assert Image.open(io.BytesIO(jpeg)).getexif().get(0x0112) == 6

    digest = (
        await upload(csrf_client, ids["alpha-model"], jpeg, "image/jpeg")
    ).json()["background"]
    img = stored((await csrf_client.get(f"/api/wefts/background/{digest}")).content)

    assert img.size == (40, 80), "the orientation should have been applied, not kept"
    assert not img.getexif(), "no EXIF at all should survive, GPS least of all"
    assert "exif" not in img.info


@pytest.mark.asyncio
async def test_transparency_survives(csrf_client, session):
    await signed_in(csrf_client, session)
    ids = await make_wefts(csrf_client, "alpha-model")

    digest = (
        await upload(
            csrf_client, ids["alpha-model"],
            picture(mode="RGBA", colour=(10, 200, 220, 0)),
        )
    ).json()["background"]
    img = stored((await csrf_client.get(f"/api/wefts/background/{digest}")).content)

    assert img.mode == "RGBA"
    assert img.getpixel((0, 0))[3] == 0


@pytest.mark.asyncio
async def test_a_gif_keeps_its_first_frame(csrf_client, session):
    """Backgrounds are still. A GIF is accepted for the cat memes and flattened."""
    await signed_in(csrf_client, session)
    ids = await make_wefts(csrf_client, "alpha-model")

    frames = [Image.new("RGB", (32, 32), c) for c in [(255, 0, 0), (0, 0, 255)]]
    out = io.BytesIO()
    frames[0].save(out, format="GIF", save_all=True, append_images=frames[1:], duration=100)

    response = await upload(csrf_client, ids["alpha-model"], out.getvalue(), "image/gif")
    assert response.status_code == 200
    img = stored(
        (await csrf_client.get(f"/api/wefts/background/{response.json()['background']}")).content
    )
    assert getattr(img, "n_frames", 1) == 1
    r, _, b = img.convert("RGB").getpixel((0, 0))
    assert r > 200 and b < 50


# ---- refusals ---------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("data, content_type, code", [
    (b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
     "image/svg+xml", 415),
    (b"hello", "text/plain", 415),
    (b"not a png at all", "image/png", 422),
    (b"", "image/png", 422),
    (picture(fmt="BMP"), "image/bmp", 422),  # decodable, but not on the list
    (picture(fmt="PNG")[:40], "image/png", 422),  # truncated
])
async def test_what_is_not_a_picture_is_refused(csrf_client, session, data, content_type, code):
    await signed_in(csrf_client, session)
    ids = await make_wefts(csrf_client, "alpha-model")

    response = await upload(csrf_client, ids["alpha-model"], data, content_type)
    assert response.status_code == code
    assert (await weft_named(csrf_client, "alpha-model"))["background"] is None


@pytest.mark.asyncio
async def test_an_oversized_upload_is_refused(csrf_client, session, monkeypatch):
    monkeypatch.setattr(backgrounds, "MAX_UPLOAD_BYTES", 1000)
    await signed_in(csrf_client, session)
    ids = await make_wefts(csrf_client, "alpha-model")

    response = await upload(csrf_client, ids["alpha-model"], b"\0" * 1001)
    assert response.status_code == 413


@pytest.mark.asyncio
async def test_a_decompression_bomb_is_refused(csrf_client, session, monkeypatch):
    """A small file declaring a huge canvas. Refused from the header, before decoding."""
    monkeypatch.setattr(backgrounds, "MAX_PIXELS", 100 * 100)
    await signed_in(csrf_client, session)
    ids = await make_wefts(csrf_client, "alpha-model")

    response = await upload(csrf_client, ids["alpha-model"], picture(size=(101, 100)))
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_a_picture_cannot_go_on_a_weft_that_does_not_exist(csrf_client, session):
    await signed_in(csrf_client, session)
    ids = await make_wefts(csrf_client, "alpha-model")

    response = await upload(csrf_client, "no-such-weft", picture())
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_an_upload_needs_csrf(csrf_client, session):
    """A bearer alone is not enough to change somebody's picture: without the CSRF
    header, the write is refused before anything is read."""
    await signed_in(csrf_client, session)
    ids = await make_wefts(csrf_client, "alpha-model")
    del csrf_client.headers["X-Weft-CSRF"]

    response = await upload(csrf_client, ids["alpha-model"], picture())
    assert response.status_code == 403


# ---- who may see one --------------------------------------------------------


@pytest.mark.asyncio
async def test_logged_out_gets_nothing(csrf_client, session):
    await signed_in(csrf_client, session)
    ids = await make_wefts(csrf_client, "alpha-model")
    digest = (await upload(csrf_client, ids["alpha-model"], picture())).json()["background"]

    del csrf_client.headers["Authorization"]
    response = await csrf_client.get(f"/api/wefts/background/{digest}")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_another_users_picture_is_not_found(csrf_client, session):
    """Bob's picture, asked for by Alice who knows its hash: the same 404 as a picture
    that does not exist, so the route cannot confirm what anyone else has uploaded."""
    await signed_in(csrf_client, session, username="bob")
    ids = await make_wefts(csrf_client, "alpha-model")
    digest = (await upload(csrf_client, ids["alpha-model"], picture())).json()["background"]

    await make_user(session, username="alice")
    response = await login(csrf_client, username="alice", password=PASSWORD)
    csrf_client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"

    theirs = await csrf_client.get(f"/api/wefts/background/{digest}")
    nobodys = await csrf_client.get(f"/api/wefts/background/{'0' * 64}")
    assert theirs.status_code == nobodys.status_code == 404
    assert theirs.json() == nobodys.json()

    # Nor can she dress her own weft in his by naming it: there is no route that takes
    # a hash as input. She can only upload bytes, which is hers to do.
    assert (await upload(csrf_client, ids["alpha-model"], picture())).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["../../weft.db", "ABC", "0" * 63, "g" * 64])
async def test_only_a_hash_is_a_name(csrf_client, session, bad):
    await signed_in(csrf_client, session)
    response = await csrf_client.get(f"/api/wefts/background/{bad}")
    assert response.status_code in (404, 422)


# ---- removal and the sweep --------------------------------------------------


def age(path, seconds):
    then = time.time() - seconds
    os.utime(path, (then, then))


@pytest.mark.asyncio
async def test_removing_a_picture(csrf_client, session, picture_dir):
    await signed_in(csrf_client, session)
    ids = await make_wefts(csrf_client, "alpha-model")
    digest = (await upload(csrf_client, ids["alpha-model"], picture())).json()["background"]
    age(picture_dir / f"{digest}.webp", backgrounds.SWEEP_GRACE_SECONDS + 1)

    response = await clear(csrf_client, ids["alpha-model"])
    assert response.status_code == 200
    assert response.json()["background"] is None
    assert (await csrf_client.get(f"/api/wefts/background/{digest}")).status_code == 404
    assert not (picture_dir / f"{digest}.webp").exists()


@pytest.mark.asyncio
async def test_a_shared_picture_outlives_one_of_its_wefts(csrf_client, session, picture_dir):
    await signed_in(csrf_client, session)
    ids = await make_wefts(csrf_client, "alpha-model", "zeta-model")
    digest = (await upload(csrf_client, ids["alpha-model"], picture())).json()["background"]
    await upload(csrf_client, ids["zeta-model"], picture())
    age(picture_dir / f"{digest}.webp", backgrounds.SWEEP_GRACE_SECONDS + 1)

    await clear(csrf_client, ids["alpha-model"])
    assert (picture_dir / f"{digest}.webp").is_file()
    assert (await csrf_client.get(f"/api/wefts/background/{digest}")).status_code == 200


@pytest.mark.asyncio
async def test_replacing_a_picture_sweeps_the_old_one(csrf_client, session, picture_dir):
    await signed_in(csrf_client, session)
    ids = await make_wefts(csrf_client, "alpha-model")
    old = (await upload(csrf_client, ids["alpha-model"], picture())).json()["background"]
    age(picture_dir / f"{old}.webp", backgrounds.SWEEP_GRACE_SECONDS + 1)

    new = (
        await upload(csrf_client, ids["alpha-model"], picture(colour=(0, 0, 255)))
    ).json()["background"]
    assert new != old
    assert not (picture_dir / f"{old}.webp").exists()
    assert (picture_dir / f"{new}.webp").is_file()


@pytest.mark.asyncio
async def test_the_sweep_spares_young_files_and_strangers(session, picture_dir):
    """A file younger than the grace may belong to a commit still in flight; a file
    that is not one of our names is somebody else's. Neither is touched."""
    picture_dir.mkdir(parents=True)
    young = picture_dir / f"{'a' * 64}.webp"
    old = picture_dir / f"{'b' * 64}.webp"
    stranger = picture_dir / "README.txt"
    for path in (young, old, stranger):
        path.write_bytes(b"x")
    age(old, backgrounds.SWEEP_GRACE_SECONDS + 1)
    age(stranger, backgrounds.SWEEP_GRACE_SECONDS + 1)

    assert await backgrounds.sweep(session) == 1
    assert young.exists() and stranger.exists() and not old.exists()


@pytest.mark.asyncio
async def test_deleting_a_weft_sweeps_its_picture(csrf_client, session, picture_dir):
    await signed_in(csrf_client, session)
    ids = await make_wefts(csrf_client, "alpha-model")
    digest = (await upload(csrf_client, ids["alpha-model"], picture())).json()["background"]
    age(picture_dir / f"{digest}.webp", backgrounds.SWEEP_GRACE_SECONDS + 1)

    await csrf_client.delete(f"/api/wefts/{ids['alpha-model']}")
    assert not (picture_dir / f"{digest}.webp").exists()


@pytest.mark.asyncio
async def test_the_picture_survives_the_other_settings(csrf_client, session):
    await signed_in(csrf_client, session)
    ids = await make_wefts(csrf_client, "alpha-model")
    digest = (await upload(csrf_client, ids["alpha-model"], picture())).json()["background"]

    await csrf_client.put(f"/api/wefts/{ids['alpha-model']}/colours", json={
        "colour_a": "#112233", "colour_b": "#445566",
    })
    await csrf_client.put(f"/api/wefts/{ids['alpha-model']}/engine", json={})
    await csrf_client.patch(f"/api/wefts/{ids['alpha-model']}", json={"name": "Renamed"})
    assert (await weft_named(csrf_client, "Renamed"))["background"] == digest
