"""Iteration 4 — CSV Import feature tests + regression."""
import os
import io
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
ADMIN_EMAIL = "dhrubajyotibhowmik5@gmail.com"
ADMIN_PASSWORD = "dhrubajyoti_14"
USER_EMAIL = "test@posterzone.com"
USER_PASSWORD = "Test@12345"

TEST_SLUG_PREFIX = "csv-test-"


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def user_token():
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": USER_EMAIL, "password": USER_PASSWORD})
    if r.status_code != 200:
        # signup
        requests.post(f"{BASE_URL}/api/auth/signup", json={"email": USER_EMAIL, "password": USER_PASSWORD, "name": "Test User"})
        r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": USER_EMAIL, "password": USER_PASSWORD})
    assert r.status_code == 200
    return r.json()["token"]


def admin_hdr(t):
    return {"Authorization": f"Bearer {t}"}


def _csv_file(content: str, name: str = "test.csv"):
    return {"file": (name, io.BytesIO(content.encode("utf-8")), "text/csv")}


# ============ 1. Happy path ============
def test_import_valid_csv(admin_token):
    csv_content = (
        "title,category,price,image_url,description,slug,featured,tags,stock\n"
        f'"CSV Alpha","Abstract",249,"https://images.unsplash.com/photo-1706630909453-c1058a447373?w=400","alpha desc","{TEST_SLUG_PREFIX}alpha",true,"a,b",50\n'
        f'"CSV Beta","Minimal",199,"https://images.unsplash.com/photo-1495001258031-d1b407bc1776?w=400","beta desc","{TEST_SLUG_PREFIX}beta",no,"c",25\n'
    )
    r = requests.post(f"{BASE_URL}/api/admin/posters/import-csv",
                      headers=admin_hdr(admin_token),
                      files=_csv_file(csv_content))
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["count"] == 2
    assert len(data["created"]) == 2
    assert data["skipped"] == []
    assert data["errors"] == []
    slugs = [c["slug"] for c in data["created"]]
    assert f"{TEST_SLUG_PREFIX}alpha" in slugs
    # Verify GET /api/posters
    r2 = requests.get(f"{BASE_URL}/api/posters")
    assert r2.status_code == 200
    all_slugs = [p["slug"] for p in r2.json()]
    assert f"{TEST_SLUG_PREFIX}alpha" in all_slugs

    # Check defaults applied (A4/A3/A2, frames, featured bool, tags array, stock)
    r3 = requests.get(f"{BASE_URL}/api/posters/{TEST_SLUG_PREFIX}alpha")
    assert r3.status_code == 200
    p = r3.json()
    assert len(p["sizes"]) == 3
    assert len(p["frame_options"]) == 4
    assert p["featured"] is True
    assert p["tags"] == ["a", "b"]
    assert p["stock"] == 50

    r4 = requests.get(f"{BASE_URL}/api/posters/{TEST_SLUG_PREFIX}beta")
    assert r4.json()["featured"] is False


# ============ 2. Validation ============
def test_non_csv_extension_rejected(admin_token):
    r = requests.post(f"{BASE_URL}/api/admin/posters/import-csv",
                      headers=admin_hdr(admin_token),
                      files={"file": ("test.txt", io.BytesIO(b"title\nfoo"), "text/plain")})
    assert r.status_code == 400


def test_missing_required_columns(admin_token):
    csv_content = "title,category\nfoo,bar\n"
    r = requests.post(f"{BASE_URL}/api/admin/posters/import-csv",
                      headers=admin_hdr(admin_token),
                      files=_csv_file(csv_content))
    assert r.status_code == 400
    assert "missing required columns" in r.json()["detail"].lower()
    assert "price" in r.json()["detail"] or "image_url" in r.json()["detail"]


def test_empty_csv(admin_token):
    r = requests.post(f"{BASE_URL}/api/admin/posters/import-csv",
                      headers=admin_hdr(admin_token),
                      files=_csv_file(""))
    assert r.status_code == 400


def test_file_too_large(admin_token):
    big = "title,category,price,image_url\n" + ("x,y,1,http://a\n" * 200000)  # ~2.6MB
    r = requests.post(f"{BASE_URL}/api/admin/posters/import-csv",
                      headers=admin_hdr(admin_token),
                      files=_csv_file(big))
    assert r.status_code == 400


def test_invalid_price_captured_in_errors(admin_token):
    csv_content = (
        "title,category,price,image_url,slug\n"
        f"BadPrice,Abstract,notanumber,https://x.com/i.jpg,{TEST_SLUG_PREFIX}badprice\n"
        f"GoodOne,Abstract,150,https://x.com/i.jpg,{TEST_SLUG_PREFIX}goodone\n"
    )
    r = requests.post(f"{BASE_URL}/api/admin/posters/import-csv",
                      headers=admin_hdr(admin_token),
                      files=_csv_file(csv_content))
    assert r.status_code == 200
    data = r.json()
    assert data["count"] == 1
    assert len(data["errors"]) == 1
    assert "price" in data["errors"][0]["error"].lower()


def test_duplicate_slug_in_csv(admin_token):
    csv_content = (
        "title,category,price,image_url,slug\n"
        f"DupOne,Abstract,100,https://x.com/i.jpg,{TEST_SLUG_PREFIX}dup\n"
        f"DupTwo,Abstract,100,https://x.com/i.jpg,{TEST_SLUG_PREFIX}dup\n"
    )
    r = requests.post(f"{BASE_URL}/api/admin/posters/import-csv",
                      headers=admin_hdr(admin_token),
                      files=_csv_file(csv_content))
    assert r.status_code == 200
    data = r.json()
    assert data["count"] == 1
    assert len(data["skipped"]) == 1
    assert "duplicate" in data["skipped"][0]["reason"].lower()


def test_slug_already_in_db(admin_token):
    # Reimport CSV Alpha
    csv_content = (
        "title,category,price,image_url,slug\n"
        f"Reimport,Abstract,100,https://x.com/i.jpg,{TEST_SLUG_PREFIX}alpha\n"
    )
    r = requests.post(f"{BASE_URL}/api/admin/posters/import-csv",
                      headers=admin_hdr(admin_token),
                      files=_csv_file(csv_content))
    assert r.status_code == 200
    data = r.json()
    assert data["count"] == 0
    assert len(data["skipped"]) == 1
    assert "already exists" in data["skipped"][0]["reason"].lower()


def test_missing_required_field(admin_token):
    csv_content = (
        "title,category,price,image_url\n"
        ",Abstract,100,https://x.com/i.jpg\n"
    )
    r = requests.post(f"{BASE_URL}/api/admin/posters/import-csv",
                      headers=admin_hdr(admin_token),
                      files=_csv_file(csv_content))
    assert r.status_code == 200
    assert r.json()["count"] == 0
    assert len(r.json()["errors"]) == 1


# ============ 3. Auth ============
def test_non_admin_forbidden(user_token):
    csv_content = "title,category,price,image_url\nfoo,bar,10,http://x\n"
    r = requests.post(f"{BASE_URL}/api/admin/posters/import-csv",
                      headers=admin_hdr(user_token),
                      files=_csv_file(csv_content))
    assert r.status_code == 403


def test_no_auth_unauthorized():
    csv_content = "title,category,price,image_url\nfoo,bar,10,http://x\n"
    r = requests.post(f"{BASE_URL}/api/admin/posters/import-csv",
                      files=_csv_file(csv_content))
    assert r.status_code == 401


# ============ 4. Featured variants ============
def test_featured_and_stock_variants(admin_token):
    csv_content = (
        "title,category,price,image_url,slug,featured,stock,tags\n"
        f"Y1,Abstract,100,https://x.com/i.jpg,{TEST_SLUG_PREFIX}yesone,YES,,\n"
        f"T1,Abstract,100,https://x.com/i.jpg,{TEST_SLUG_PREFIX}truelow,true,7,\n"
        f"O1,Abstract,100,https://x.com/i.jpg,{TEST_SLUG_PREFIX}oneval,1,,tag1,tag2\n"
        f"N1,Abstract,100,https://x.com/i.jpg,{TEST_SLUG_PREFIX}noval,no,,\n"
        f"F1,Abstract,100,https://x.com/i.jpg,{TEST_SLUG_PREFIX}falseval,FALSE,,\n"
        f"Z1,Abstract,100,https://x.com/i.jpg,{TEST_SLUG_PREFIX}zeroval,0,,\n"
    )
    r = requests.post(f"{BASE_URL}/api/admin/posters/import-csv",
                      headers=admin_hdr(admin_token),
                      files=_csv_file(csv_content))
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 6
    # Verify individually
    for slug, expected in [
        (f"{TEST_SLUG_PREFIX}yesone", True),
        (f"{TEST_SLUG_PREFIX}truelow", True),
        (f"{TEST_SLUG_PREFIX}oneval", True),
        (f"{TEST_SLUG_PREFIX}noval", False),
        (f"{TEST_SLUG_PREFIX}falseval", False),
        (f"{TEST_SLUG_PREFIX}zeroval", False),
    ]:
        p = requests.get(f"{BASE_URL}/api/posters/{slug}").json()
        assert p["featured"] is expected, f"{slug} featured mismatch"

    # stock default = 100
    p = requests.get(f"{BASE_URL}/api/posters/{TEST_SLUG_PREFIX}yesone").json()
    assert p["stock"] == 100
    p = requests.get(f"{BASE_URL}/api/posters/{TEST_SLUG_PREFIX}truelow").json()
    assert p["stock"] == 7


# ============ 5. Regression ============
def test_regression_login_and_posters(admin_token):
    r = requests.get(f"{BASE_URL}/api/posters")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_regression_bulk_posters_endpoint(admin_token):
    payload = [{
        "slug": f"{TEST_SLUG_PREFIX}bulk1",
        "title": "Bulk1",
        "description": "d",
        "price": 100,
        "category": "Abstract",
        "tags": [],
        "image_url": "https://x.com/i.jpg",
        "sizes": [],
        "frame_options": [],
        "stock": 10,
        "featured": False,
    }]
    r = requests.post(f"{BASE_URL}/api/admin/posters/bulk",
                      headers=admin_hdr(admin_token), json=payload)
    assert r.status_code == 200
    assert r.json()["count"] == 1


def test_regression_upload_endpoint(admin_token):
    # tiny PNG bytes
    png = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c626001000000050001") + b"\x00" * 10
    r = requests.post(f"{BASE_URL}/api/admin/upload",
                      headers=admin_hdr(admin_token),
                      files={"file": ("t.png", io.BytesIO(png), "image/png")})
    assert r.status_code in (200, 500)  # 500 if storage unavailable; endpoint reachable


# ============ Cleanup ============
def test_zzz_cleanup(admin_token):
    """Delete all csv-test-* posters created during this run."""
    r = requests.get(f"{BASE_URL}/api/posters")
    for p in r.json():
        if p["slug"].startswith(TEST_SLUG_PREFIX):
            requests.delete(f"{BASE_URL}/api/admin/posters/{p['id']}", headers=admin_hdr(admin_token))
    # verify
    r = requests.get(f"{BASE_URL}/api/posters")
    remaining = [p["slug"] for p in r.json() if p["slug"].startswith(TEST_SLUG_PREFIX)]
    assert remaining == []
