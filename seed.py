"""Idempotent fictional content used by a new SchoolNet SQLite database."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _event_parts(value: datetime) -> tuple[str, str, str]:
    return (
        value.strftime("%A, %d %B"),
        value.strftime("%b").upper(),
        str(value.day),
    )


def seed_database(connection: sqlite3.Connection) -> bool:
    """Seed only a previously uninitialized database and return whether it changed."""
    already_seeded = connection.execute(
        "SELECT 1 FROM app_meta WHERE key = 'seed_version'"
    ).fetchone()
    if already_seeded:
        return False

    initialized_at = datetime.now(timezone.utc).replace(microsecond=0)
    event_one = (initialized_at + timedelta(days=2)).replace(hour=15, minute=30, second=0)
    event_two = (initialized_at + timedelta(days=4)).replace(hour=16, minute=0, second=0)
    event_three = (initialized_at + timedelta(days=7)).replace(hour=9, minute=0, second=0)

    profiles = [
        (
            "abebe",
            "Mr. Abebe",
            "Physics Teacher",
            "Helping students turn careful practice into confident progress.",
            "MA",
            "#7B9175",
            "Science Department",
        ),
        (
            "sara",
            "Ms. Sara",
            "Science Club Coordinator",
            "Making space for questions, experiments, and imaginative thinking.",
            "MS",
            "#8B769A",
            "Student Life",
        ),
        (
            "dan",
            "Coach Dan",
            "Sports Director",
            "Building teamwork, courage, and joy beyond the classroom.",
            "CD",
            "#C98458",
            "Athletics",
        ),
    ]
    posts = [
        (
            "abebe",
            "Academics",
            "A little preparation. A lot of possibility.",
            "Our next physics revision session is coming up. Bring your questions, your formula sheet, and a friend as we prepare together for the upcoming exam.\n\nA little practice today can make a big difference tomorrow. Check the school calendar for the details.",
            _iso_utc(initialized_at),
            1,
            None,
        ),
        (
            "sara",
            "Clubs",
            "Big ideas start with a little curiosity.",
            "Science Club registration is now open. Join us for practical investigations, thoughtful conversation, and a welcoming first meetup this week.",
            _iso_utc(initialized_at - timedelta(hours=3)),
            0,
            "/static/images/science-lab.jpg",
        ),
        (
            "dan",
            "Sports",
            "Your next chapter starts on the field.",
            "Football trials are approaching. All experience levels are welcome—come ready to move, learn, and support your teammates.",
            _iso_utc(initialized_at - timedelta(hours=7)),
            0,
            None,
        ),
        (
            "abebe",
            "General",
            "A quiet place to focus is waiting for you.",
            "The library study area will stay open after lessons this week for anyone looking for a calm space to read, revise, or work with classmates.",
            _iso_utc(initialized_at - timedelta(days=1)),
            0,
            None,
        ),
    ]
    first_label, first_month, first_day = _event_parts(event_one)
    second_label, second_month, second_day = _event_parts(event_two)
    third_label, third_month, third_day = _event_parts(event_three)
    events = [
        (
            1,
            "Physics revision session",
            "Academics",
            _iso_utc(event_one),
            first_label,
            first_month,
            first_day,
            "3:30 PM",
            "Physics Lab",
            "A friendly revision session focused on key exam topics and student questions.",
        ),
        (
            2,
            "Science Club meetup",
            "Clubs",
            _iso_utc(event_two),
            second_label,
            second_month,
            second_day,
            "4:00 PM",
            "Innovation Room",
            "Meet fellow curious minds, plan experiments, and register for the term ahead.",
        ),
        (
            3,
            "Inter-house sports day",
            "Sports",
            _iso_utc(event_three),
            third_label,
            third_month,
            third_day,
            "9:00 AM",
            "Main Field",
            "A day of spirited, inclusive competition for every house and every supporter.",
        ),
    ]

    with connection:
        connection.executemany(
            """
            INSERT INTO profiles (id, name, role, bio, initials, color, department)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            profiles,
        )
        connection.executemany(
            """
            INSERT INTO posts (profile_id, category, title, content, created_at, pinned, image)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            posts,
        )
        connection.executemany(
            """
            INSERT INTO events (id, title, category, date, date_label, month, day, time, location, description)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            events,
        )
        connection.execute(
            "INSERT INTO app_meta (key, value) VALUES ('seed_version', '1')"
        )
        connection.execute(
            "INSERT INTO app_meta (key, value) VALUES ('initialized_at', ?)", (_iso_utc(initialized_at),)
        )
    return True
