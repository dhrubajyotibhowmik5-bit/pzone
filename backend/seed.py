"""Seed script — creates admin user + sample poster catalog on first boot."""
import os
import uuid
from datetime import datetime, timezone
import bcrypt


def _hash(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


DEFAULT_SIZES = [
    {"label": "A4 (8x11 in)", "price_delta": 0.0},
    {"label": "A3 (12x17 in)", "price_delta": 200.0},
    {"label": "A2 (17x23 in)", "price_delta": 500.0},
]
DEFAULT_FRAMES = []


POSTERS = [
    # Movies
    {"slug": "pulp-fiction-noir", "title": "Pulp Fiction — Neon Noir", "category": "Movies",
     "price": 299, "tags": ["cinema", "cult", "tarantino"], "featured": True,
     "image_url": "https://images.unsplash.com/photo-1489599735734-79b4169c2a78?auto=format&fit=crop&w=900&q=80",
     "description": "A neon-drenched tribute to Tarantino's cult classic. Bold typography, retro palette."},
    {"slug": "cinema-marquee-lights", "title": "Cinema Marquee", "category": "Movies",
     "price": 349, "tags": ["cinema", "vintage"], "featured": False,
     "image_url": "https://images.unsplash.com/photo-1440404653325-ab127d49abc1?auto=format&fit=crop&w=900&q=80",
     "description": "Classic marquee bulbs — a nostalgic ode to the golden age of cinema."},
    {"slug": "vintage-film-reel", "title": "Reel Story", "category": "Movies",
     "price": 279, "tags": ["cinema", "retro"], "featured": False,
     "image_url": "https://images.unsplash.com/photo-1478720568477-152d9b164e26?auto=format&fit=crop&w=900&q=80",
     "description": "Grainy film reel print for the analog purist. Muted tones, museum-grade paper."},

    # Anime
    {"slug": "tokyo-neon-night", "title": "Tokyo Neon", "category": "Anime",
     "price": 399, "tags": ["japan", "cyberpunk"], "featured": True,
     "image_url": "https://images.unsplash.com/photo-1542051841857-5f90071e7989?auto=format&fit=crop&w=900&q=80",
     "description": "Rain-soaked Shibuya at midnight. Vaporwave grade, thick ink outlines."},
    {"slug": "sakura-samurai", "title": "Sakura Samurai", "category": "Anime",
     "price": 429, "tags": ["japan", "warrior"], "featured": False,
     "image_url": "https://images.unsplash.com/photo-1522383225653-ed111181a951?auto=format&fit=crop&w=900&q=80",
     "description": "Cherry-blossom warrior in a lone frame. Ukiyo-e inspired composition."},

    # Music
    {"slug": "vinyl-groove", "title": "Vinyl Groove", "category": "Music",
     "price": 259, "tags": ["vinyl", "retro"], "featured": True,
     "image_url": "https://images.unsplash.com/photo-1470225620780-dba8ba36b745?auto=format&fit=crop&w=900&q=80",
     "description": "For the audiophile's wall. Deep black grooves, warm amber glow."},
    {"slug": "guitar-heroes", "title": "Guitar Heroes", "category": "Music",
     "price": 289, "tags": ["rock", "guitar"], "featured": False,
     "image_url": "https://images.unsplash.com/photo-1510915361894-db8b60106cb1?auto=format&fit=crop&w=900&q=80",
     "description": "A tribute to six-string legends. High-contrast monochrome print."},

    # Motivational
    {"slug": "keep-going", "title": "Keep Going", "category": "Motivational",
     "price": 199, "tags": ["quote", "typography"], "featured": True,
     "image_url": "https://images.unsplash.com/photo-1493612276216-ee3925520721?auto=format&fit=crop&w=900&q=80",
     "description": "Two words. Every morning. Bold serif on textured paper."},
    {"slug": "hustle-quiet", "title": "Hustle Quietly", "category": "Motivational",
     "price": 219, "tags": ["quote", "minimal"], "featured": False,
     "image_url": "https://images.unsplash.com/photo-1454165804606-c3d57bc86b40?auto=format&fit=crop&w=900&q=80",
     "description": "Understated typographic reminder for the deep-work desk."},

    # Sports
    {"slug": "basketball-dunk", "title": "Above the Rim", "category": "Sports",
     "price": 329, "tags": ["basketball", "action"], "featured": False,
     "image_url": "https://images.unsplash.com/photo-1546519638-68e109498ffc?auto=format&fit=crop&w=900&q=80",
     "description": "Frozen mid-air. Ink-splash edges. For hoop dreamers."},
    {"slug": "football-fever", "title": "Football Fever", "category": "Sports",
     "price": 339, "tags": ["football", "stadium"], "featured": True,
     "image_url": "https://images.unsplash.com/photo-1508098682722-e99c43a406b2?auto=format&fit=crop&w=900&q=80",
     "description": "Roaring stadium in silhouette. High-contrast red and black."},

    # Abstract
    {"slug": "chromatic-strokes", "title": "Chromatic Strokes", "category": "Abstract",
     "price": 359, "tags": ["color", "art"], "featured": True,
     "image_url": "https://images.unsplash.com/photo-1706630909453-c1058a447373?auto=format&fit=crop&w=900&q=80",
     "description": "Wild colour play — hand-painted digital print. Adds life to any wall."},
    {"slug": "red-white-blue", "title": "RWB Composition", "category": "Abstract",
     "price": 379, "tags": ["color", "modern"], "featured": False,
     "image_url": "https://images.unsplash.com/photo-1705420292539-ac64810ef8a1?auto=format&fit=crop&w=900&q=80",
     "description": "Bold red-white-blue geometry. Statement piece for modern interiors."},

    # Nature
    {"slug": "misty-mountains", "title": "Misty Mountains", "category": "Nature",
     "price": 269, "tags": ["landscape", "calm"], "featured": False,
     "image_url": "https://images.unsplash.com/photo-1464822759023-fed622ff2c3b?auto=format&fit=crop&w=900&q=80",
     "description": "Fog-shrouded peaks. A slow-breath print for the study."},
    {"slug": "forest-canopy", "title": "Forest Canopy", "category": "Nature",
     "price": 249, "tags": ["forest", "green"], "featured": True,
     "image_url": "https://images.unsplash.com/photo-1441974231531-c6227db76b6e?auto=format&fit=crop&w=900&q=80",
     "description": "Looking up through the canopy — deep greens, dappled light."},

    # Minimal
    {"slug": "less-is-more", "title": "Less Is More", "category": "Minimal",
     "price": 189, "tags": ["typography", "clean"], "featured": True,
     "image_url": "https://images.unsplash.com/photo-1495001258031-d1b407bc1776?auto=format&fit=crop&w=900&q=80",
     "description": "Three words. One line. Everything you need."},
    {"slug": "single-line", "title": "Single Line", "category": "Minimal",
     "price": 209, "tags": ["line-art"], "featured": False,
     "image_url": "https://images.unsplash.com/photo-1513519245088-0e12902e5a38?auto=format&fit=crop&w=900&q=80",
     "description": "One continuous line — the essence of form."},

    # Travel
    {"slug": "paris-in-ink", "title": "Paris In Ink", "category": "Travel",
     "price": 289, "tags": ["paris", "city"], "featured": False,
     "image_url": "https://images.unsplash.com/photo-1502602898657-3e91760cbb34?auto=format&fit=crop&w=900&q=80",
     "description": "The city of light — reduced to sharp ink strokes."},
    {"slug": "tokyo-alleys", "title": "Tokyo Alleys", "category": "Travel",
     "price": 299, "tags": ["tokyo", "city"], "featured": True,
     "image_url": "https://images.unsplash.com/photo-1503899036084-c55cdd92da26?auto=format&fit=crop&w=900&q=80",
     "description": "Narrow lantern-lit lanes. Deep blues and warm reds."},

    # Gaming
    {"slug": "pixel-warrior", "title": "Pixel Warrior", "category": "Gaming",
     "price": 269, "tags": ["retro-games", "8bit"], "featured": True,
     "image_url": "https://images.unsplash.com/photo-1550745165-9bc0b252726f?auto=format&fit=crop&w=900&q=80",
     "description": "Sprite-era hero, framed like a modern art piece."},
    {"slug": "arcade-glow", "title": "Arcade Glow", "category": "Gaming",
     "price": 289, "tags": ["arcade", "neon"], "featured": False,
     "image_url": "https://images.unsplash.com/photo-1493711662062-fa541adb3fc8?auto=format&fit=crop&w=900&q=80",
     "description": "The purr of the CRT, the neon buzz. A gamer's shrine."},
]


async def seed_all(db):
    # Seed admin (idempotent — always ensure role=admin and password matches env)
    admin_email = os.environ.get("ADMIN_EMAIL", "admin@posterzone.com")
    admin_password = os.environ.get("ADMIN_PASSWORD", "Admin@12345")
    existing_admin = await db.users.find_one({"email": admin_email})
    if not existing_admin:
        await db.users.insert_one({
            "user_id": f"user_{uuid.uuid4().hex[:12]}",
            "email": admin_email,
            "name": "Poster Zone Admin",
            "picture": None,
            "role": "admin",
            "provider": "email",
            "password_hash": _hash(admin_password),
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    else:
        await db.users.update_one(
            {"email": admin_email},
            {"$set": {"role": "admin", "password_hash": _hash(admin_password)}},
        )

    # Also demote any old admin@posterzone.com to a regular user (clean up dev seed)
    if admin_email != "admin@posterzone.com":
        await db.users.update_one(
            {"email": "admin@posterzone.com"},
            {"$set": {"role": "user"}},
        )

    # Seed test user
    test_email = "test@posterzone.com"
    if not await db.users.find_one({"email": test_email}):
        await db.users.insert_one({
            "user_id": f"user_{uuid.uuid4().hex[:12]}",
            "email": test_email,
            "name": "Test Customer",
            "picture": None,
            "role": "user",
            "provider": "email",
            "password_hash": _hash("Test@12345"),
            "created_at": datetime.now(timezone.utc).isoformat(),
        })

    # Seed posters (idempotent by slug)
    for p in POSTERS:
        if await db.posters.find_one({"slug": p["slug"]}):
            continue
        doc = {
            "id": str(uuid.uuid4()),
            "slug": p["slug"],
            "title": p["title"],
            "description": p["description"],
            "price": p["price"],
            "category": p["category"],
            "tags": p.get("tags", []),
            "image_url": p["image_url"],
            "sizes": DEFAULT_SIZES,
            "frame_options": DEFAULT_FRAMES,
            "stock": 100,
            "featured": p.get("featured", False),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.posters.insert_one(doc)
