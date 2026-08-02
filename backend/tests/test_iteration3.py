"""Iteration 3 backend tests: new admin credentials, bulk upload, bulk poster create."""
import os
import base64
import uuid
import pytest
import requests

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
API = f"{BASE_URL}/api"

NEW_ADMIN_EMAIL = "dhrubajyotibhowmik5@gmail.com"
NEW_ADMIN_PASSWORD = "dhrubajyoti_14"
OLD_ADMIN_EMAIL = "admin@posterzone.com"
OLD_ADMIN_PASSWORD = "Admin@12345"
USER_EMAIL = "test@posterzone.com"
USER_PASSWORD = "Test@12345"


def _auth(t): return {"Authorization": f"Bearer {t}"}


def _tiny_png() -> bytes:
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg=="
    )


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(f"{API}/auth/login", json={"email": NEW_ADMIN_EMAIL, "password": NEW_ADMIN_PASSWORD}, timeout=15)
    assert r.status_code == 200, f"New admin login failed: {r.status_code} {r.text}"
    data = r.json()
    assert data["user"]["role"] == "admin"
    return data["token"]


@pytest.fixture(scope="module")
def user_token():
    r = requests.post(f"{API}/auth/login", json={"email": USER_EMAIL, "password": USER_PASSWORD}, timeout=15)
    if r.status_code != 200:
        r = requests.post(f"{API}/auth/signup", json={"email": USER_EMAIL, "password": USER_PASSWORD, "name": "Test"}, timeout=15)
    assert r.status_code == 200
    return r.json()["token"]


# ---------- Admin credential change ----------
class TestNewAdminCredentials:
    def test_new_admin_login(self):
        r = requests.post(f"{API}/auth/login", json={"email": NEW_ADMIN_EMAIL, "password": NEW_ADMIN_PASSWORD}, timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["user"]["email"] == NEW_ADMIN_EMAIL
        assert data["user"]["role"] == "admin"
        assert isinstance(data["token"], str) and len(data["token"]) > 0

    def test_old_admin_demoted(self):
        # Should still be able to login (user still exists) but with role=user
        r = requests.post(f"{API}/auth/login", json={"email": OLD_ADMIN_EMAIL, "password": OLD_ADMIN_PASSWORD}, timeout=15)
        # If login works, role must be 'user' (not admin)
        if r.status_code == 200:
            assert r.json()["user"]["role"] == "user", "Old admin should be demoted to role=user"
        else:
            # Acceptable if user removed. But per spec, should exist and be demoted.
            pytest.fail(f"Old admin login unexpected status {r.status_code}: {r.text}")

    def test_wrong_admin_password_rejected(self):
        r = requests.post(f"{API}/auth/login", json={"email": NEW_ADMIN_EMAIL, "password": "wrong_password"}, timeout=15)
        assert r.status_code == 401


# ---------- Bulk upload ----------
class TestBulkUpload:
    def test_bulk_upload_requires_admin(self, user_token):
        files = [("files", ("a.png", _tiny_png(), "image/png"))]
        r = requests.post(f"{API}/admin/upload/bulk", files=files, headers=_auth(user_token), timeout=30)
        assert r.status_code == 403

    def test_bulk_upload_unauth(self):
        files = [("files", ("a.png", _tiny_png(), "image/png"))]
        r = requests.post(f"{API}/admin/upload/bulk", files=files, timeout=30)
        assert r.status_code == 401

    def test_bulk_upload_3_pngs_and_public_get(self, admin_token):
        png = _tiny_png()
        files = [
            ("files", ("a.png", png, "image/png")),
            ("files", ("b.png", png, "image/png")),
            ("files", ("c.png", png, "image/png")),
        ]
        r = requests.post(f"{API}/admin/upload/bulk", files=files, headers=_auth(admin_token), timeout=60)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "results" in data
        assert len(data["results"]) == 3
        urls = []
        for row in data["results"]:
            assert "filename" in row
            assert "url" in row, f"row missing url: {row}"
            assert row["url"].startswith("/api/files/")
            urls.append(row["url"])

        # verify each URL fetches successfully
        for u in urls:
            g = requests.get(f"{BASE_URL}{u}", timeout=30)
            assert g.status_code == 200
            assert g.headers.get("Content-Type", "").startswith("image/")
        # cache urls for next test on module state
        TestBulkUpload._uploaded_urls = urls

    def test_bulk_upload_too_many_rejected(self, admin_token):
        png = _tiny_png()
        files = [("files", (f"f{i}.png", png, "image/png")) for i in range(31)]
        r = requests.post(f"{API}/admin/upload/bulk", files=files, headers=_auth(admin_token), timeout=90)
        assert r.status_code == 400


# ---------- Bulk create posters ----------
class TestBulkCreatePosters:
    def test_bulk_create_and_verify(self, admin_token):
        # Upload 2 images first
        png = _tiny_png()
        files = [
            ("files", ("bulk1.png", png, "image/png")),
            ("files", ("bulk2.png", png, "image/png")),
        ]
        up = requests.post(f"{API}/admin/upload/bulk", files=files, headers=_auth(admin_token), timeout=60)
        assert up.status_code == 200
        urls = [r["url"] for r in up.json()["results"]]

        base_slug = f"test-bulk-{uuid.uuid4().hex[:6]}"
        payload = [
            {
                "slug": f"{base_slug}-1", "title": "TEST Bulk 1", "description": "d1",
                "price": 199, "category": "Abstract", "tags": ["test"], "image_url": urls[0],
                "sizes": [{"label": "A4", "price_delta": 0}], "frame_options": [{"label": "None", "price_delta": 0}],
                "stock": 10, "featured": False,
            },
            {
                "slug": f"{base_slug}-2", "title": "TEST Bulk 2", "description": "d2",
                "price": 249, "category": "Minimal", "tags": ["test"], "image_url": urls[1],
                "sizes": [{"label": "A4", "price_delta": 0}], "frame_options": [{"label": "None", "price_delta": 0}],
                "stock": 5, "featured": False,
            },
        ]
        r = requests.post(f"{API}/admin/posters/bulk", json=payload, headers=_auth(admin_token), timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["count"] == 2
        assert len(data["created"]) == 2
        assert data["errors"] == []

        # Verify appear in listing
        listing = requests.get(f"{API}/posters?limit=500", timeout=15)
        assert listing.status_code == 200
        slugs = [p["slug"] for p in listing.json()]
        assert f"{base_slug}-1" in slugs
        assert f"{base_slug}-2" in slugs

        # cleanup
        for p in data["created"]:
            requests.delete(f"{API}/admin/posters/{p['id']}", headers=_auth(admin_token), timeout=10)

    def test_bulk_create_duplicate_slug_captured_in_errors(self, admin_token):
        base_slug = f"test-dup-{uuid.uuid4().hex[:6]}"
        payload = [
            {"slug": base_slug, "title": "TEST Dup 1", "description": "d", "price": 100,
             "category": "Minimal", "tags": [], "image_url": "https://x.jpg",
             "sizes": [], "frame_options": [], "stock": 1, "featured": False},
            {"slug": base_slug, "title": "TEST Dup 2", "description": "d", "price": 100,
             "category": "Minimal", "tags": [], "image_url": "https://x.jpg",
             "sizes": [], "frame_options": [], "stock": 1, "featured": False},
            {"slug": f"{base_slug}-ok", "title": "TEST Dup OK", "description": "d", "price": 100,
             "category": "Minimal", "tags": [], "image_url": "https://x.jpg",
             "sizes": [], "frame_options": [], "stock": 1, "featured": False},
        ]
        r = requests.post(f"{API}/admin/posters/bulk", json=payload, headers=_auth(admin_token), timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["count"] == 2, f"expected 2 created (1st + 3rd), got {data}"
        assert len(data["errors"]) == 1
        assert data["errors"][0]["slug"] == base_slug

        # cleanup
        for p in data["created"]:
            requests.delete(f"{API}/admin/posters/{p['id']}", headers=_auth(admin_token), timeout=10)

    def test_bulk_create_requires_admin(self, user_token):
        payload = [{"slug": "x", "title": "x", "description": "x", "price": 1,
                    "category": "x", "tags": [], "image_url": "x",
                    "sizes": [], "frame_options": [], "stock": 1, "featured": False}]
        r = requests.post(f"{API}/admin/posters/bulk", json=payload, headers=_auth(user_token), timeout=15)
        assert r.status_code == 403


# ---------- Regression ----------
class TestRegression:
    def test_health(self):
        r = requests.get(f"{API}/", timeout=10)
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_posters_list(self):
        r = requests.get(f"{API}/posters", timeout=10)
        assert r.status_code == 200
        assert isinstance(r.json(), list)
        assert len(r.json()) > 0

    def test_single_upload_still_works(self, admin_token):
        files = {"file": ("t.png", _tiny_png(), "image/png")}
        r = requests.post(f"{API}/admin/upload", files=files, headers=_auth(admin_token), timeout=30)
        assert r.status_code == 200
        assert r.json()["url"].startswith("/api/files/")

    def test_wishlist_still_works(self, user_token):
        r = requests.get(f"{API}/wishlist", headers=_auth(user_token), timeout=10)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_checkout_still_works(self, user_token):
        payload = {
            "items": [{"poster_id": "p1", "title": "T", "image_url": "https://x.jpg",
                       "size": "A4", "frame": "None", "quantity": 1, "unit_price": 499}],
            "shipping_name": "T", "shipping_email": USER_EMAIL, "shipping_phone": "9",
            "shipping_address": "1", "shipping_city": "D", "shipping_state": "D",
            "shipping_pincode": "110001", "origin_url": BASE_URL,
        }
        r = requests.post(f"{API}/orders/checkout", json=payload, headers=_auth(user_token), timeout=30)
        assert r.status_code == 200
        assert "checkout_url" in r.json()
