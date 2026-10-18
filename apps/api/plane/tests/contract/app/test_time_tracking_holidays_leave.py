# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""FORK: time-tracking-v2 holidays/leave (PSR-122) — holiday calendars, holidays, per-person
calendar assignment, leave, and how they reduce Expected hours (reports, timesheet, CSV).

World: ``s2`` (Dhaka workspace; the test user is the workspace admin). Range 1–7 Oct 2026:
Thu 1, Fri 2, Sat 3, Sun 4, Mon 5, Tue 6, Wed 7 — five 8 h working days = 40 h per person.
"""

import uuid

import pytest
from freezegun import freeze_time

from plane.db.models import (
    Holiday,
    HolidayCalendar,
    MemberHolidayCalendar,
    MemberLeave,
    TimeTrackingAuditLog,
)

from .time_tracking_s2_support import (  # noqa: F401 (autouse fixture)
    _load_urlconf,
    client_for,
    make_user,
    ws_url,
)
from .time_tracking_s5_support import AFTER_RANGE, H, OCT, log, member_row, report, set_tracking

pytestmark = [pytest.mark.contract, pytest.mark.django_db]

DAY = 8 * H


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def call(user, method, s, path, body=None, expect=None):
    r = (
        getattr(client_for(user), method)(ws_url(s, path), body or {}, format="json")
        if method != "get"
        else (client_for(user).get(ws_url(s, path)))
    )
    if expect is not None:
        assert r.status_code == expect, r.content
    return r


def make_calendar(s, name="Main", default=None):
    r = call(
        s["user"],
        "post",
        s,
        "time/holiday-calendars/",
        {"name": name, **({} if default is None else {"is_default": default})},
        201,
    )
    return r.data


def add_holiday(s, calendar, day, name="Holiday", portion="full"):
    return call(
        s["user"],
        "post",
        s,
        f"time/holiday-calendars/{calendar['id']}/holidays/",
        {"date": str(day), "name": name, "portion": portion},
        201,
    ).data


def add_leave(s, user, start, end=None, portion="full", note="", expect=201):
    return call(
        s["user"],
        "post",
        s,
        "time/leave/",
        {
            "user_id": str(user.id),
            "start_date": str(start),
            "end_date": str(end or start),
            "portion": portion,
            "note": note,
        },
        expect,
    )


def assign(s, user, calendar):
    return call(
        s["user"],
        "put",
        s,
        f"time/members/{user.id}/holiday-calendar/",
        {"calendar_id": None if calendar is None else calendar["id"]},
        200,
    )


def code(r):
    return r.json()["code"]


def audits(s, target):
    return list(
        TimeTrackingAuditLog.objects.filter(workspace=s["workspace"], target_type=target).order_by("created_at")
    )


def one_person_expected(s, user, frm="2026-10-01", to="2026-10-07"):
    with freeze_time(AFTER_RANGE):
        return report(s["user"], s, frm, to, user=user.id)


# ---------------------------------------------------------------------------
# permissions
# ---------------------------------------------------------------------------


class TestPermissions:
    def _world(self, s2):
        member = make_user(s2["workspace"], 15, "Mem")
        guest = make_user(s2["workspace"], 5, "Guest")
        cal = make_calendar(s2)
        hol = add_holiday(s2, cal, OCT[2])
        leave = add_leave(s2, member, OCT[5]).data
        return member, guest, cal, hol, leave

    def test_guest_is_forbidden_everywhere(self, s2):
        member, guest, cal, hol, leave = self._world(s2)
        cid, hid, lid = cal["id"], hol["id"], leave["id"]
        calls = [
            ("get", "time/holiday-calendars/", None),
            ("post", "time/holiday-calendars/", {"name": "X"}),
            ("patch", f"time/holiday-calendars/{cid}/", {"name": "Y"}),
            ("delete", f"time/holiday-calendars/{cid}/", None),
            ("get", f"time/holiday-calendars/{cid}/holidays/", None),
            ("post", f"time/holiday-calendars/{cid}/holidays/", {"date": "2026-10-09", "name": "X"}),
            ("patch", f"time/holiday-calendars/{cid}/holidays/{hid}/", {"name": "Y"}),
            ("delete", f"time/holiday-calendars/{cid}/holidays/{hid}/", None),
            ("put", f"time/members/{member.id}/holiday-calendar/", {"calendar_id": None}),
            ("get", "time/leave/", None),
            ("post", "time/leave/", {"user_id": str(member.id), "start_date": "2026-10-20", "end_date": "2026-10-20"}),
            ("patch", f"time/leave/{lid}/", {"note": "x"}),
            ("delete", f"time/leave/{lid}/", None),
        ]
        for method, path, body in calls:
            assert call(guest, method, s2, path, body).status_code == 403, (method, path)

    def test_member_reads_calendars_and_holidays_but_cannot_write(self, s2):
        member, _guest, cal, hol, leave = self._world(s2)
        cid, hid, lid = cal["id"], hol["id"], leave["id"]
        assert call(member, "get", s2, "time/holiday-calendars/").status_code == 200
        assert call(member, "get", s2, f"time/holiday-calendars/{cid}/holidays/").status_code == 200
        writes = [
            ("post", "time/holiday-calendars/", {"name": "X"}),
            ("patch", f"time/holiday-calendars/{cid}/", {"name": "Y"}),
            ("delete", f"time/holiday-calendars/{cid}/", None),
            ("post", f"time/holiday-calendars/{cid}/holidays/", {"date": "2026-10-09", "name": "X"}),
            ("patch", f"time/holiday-calendars/{cid}/holidays/{hid}/", {"name": "Y"}),
            ("delete", f"time/holiday-calendars/{cid}/holidays/{hid}/", None),
            ("put", f"time/members/{member.id}/holiday-calendar/", {"calendar_id": None}),
            ("post", "time/leave/", {"user_id": str(member.id), "start_date": "2026-10-20", "end_date": "2026-10-20"}),
            ("patch", f"time/leave/{lid}/", {"note": "x"}),
            ("delete", f"time/leave/{lid}/", None),
        ]
        for method, path, body in writes:
            assert call(member, method, s2, path, body).status_code == 403, (method, path)
        # nothing changed
        assert HolidayCalendar.objects.filter(workspace=s2["workspace"]).count() == 1
        assert Holiday.objects.filter(calendar_id=cid).count() == 1
        assert MemberLeave.objects.filter(workspace=s2["workspace"]).count() == 1

    def test_admin_does_all_of_it(self, s2):
        member, _guest, cal, hol, leave = self._world(s2)
        assert call(
            s2["user"], "patch", s2, f"time/holiday-calendars/{cal['id']}/holidays/{hol['id']}/", {"name": "New"}, 200
        )
        assert call(s2["user"], "patch", s2, f"time/leave/{leave['id']}/", {"note": "x"}, 200)
        assert call(s2["user"], "delete", s2, f"time/leave/{leave['id']}/", None, 204)

    def test_other_workspaces_calendar_is_not_found(self, s2, create_user):
        from plane.db.models import Workspace, WorkspaceMember

        other = Workspace.objects.create(name="Other", slug="other-ws-leave", owner=create_user)
        WorkspaceMember.objects.create(workspace=other, member=create_user, role=20, is_active=True)
        foreign = HolidayCalendar.objects.create(workspace=other, name="Foreign", is_default=True)
        assert call(s2["user"], "get", s2, f"time/holiday-calendars/{foreign.id}/holidays/").status_code == 404
        assert call(s2["user"], "patch", s2, f"time/holiday-calendars/{foreign.id}/", {"name": "x"}).status_code == 404
        assert call(s2["user"], "delete", s2, f"time/holiday-calendars/{foreign.id}/").status_code == 404
        member = make_user(s2["workspace"], 15, "Mem")
        r = call(s2["user"], "put", s2, f"time/members/{member.id}/holiday-calendar/", {"calendar_id": str(foreign.id)})
        assert r.status_code == 400 and code(r) == "invalid_calendar"


# ---------------------------------------------------------------------------
# calendars
# ---------------------------------------------------------------------------


class TestCalendars:
    def test_first_calendar_is_always_default(self, s2):
        first = make_calendar(s2, "Canada", default=False)
        assert first["is_default"] is True
        second = make_calendar(s2, "Bangladesh")
        assert second["is_default"] is False
        listing = call(s2["user"], "get", s2, "time/holiday-calendars/", None, 200).data
        assert [(c["name"], c["is_default"]) for c in listing] == [("Canada", True), ("Bangladesh", False)]

    def test_listing_is_default_first_then_name_with_member_counts(self, s2):
        make_calendar(s2, "Zeta")  # default
        alpha = make_calendar(s2, "Alpha")
        mem = make_user(s2["workspace"], 15, "A")
        make_user(s2["workspace"], 15, "B")
        make_user(s2["workspace"], 5, "Guest")  # guests are not counted
        assign(s2, mem, alpha)
        listing = call(s2["user"], "get", s2, "time/holiday-calendars/", None, 200).data
        assert [c["name"] for c in listing] == ["Zeta", "Alpha"]
        by_name = {c["name"]: c for c in listing}
        assert by_name["Alpha"]["member_count"] == 1
        # admin + B fall back to the default
        assert by_name["Zeta"]["member_count"] == 2

    def test_setting_a_new_default_moves_the_flag(self, s2):
        a = make_calendar(s2, "A")
        b = make_calendar(s2, "B", default=True)
        assert b["is_default"] is True
        flags = {c.name: c.is_default for c in HolidayCalendar.objects.filter(workspace=s2["workspace"])}
        assert flags == {"A": False, "B": True}
        r = call(s2["user"], "patch", s2, f"time/holiday-calendars/{a['id']}/", {"is_default": True}, 200)
        assert r.data["is_default"] is True
        flags = {c.name: c.is_default for c in HolidayCalendar.objects.filter(workspace=s2["workspace"])}
        assert flags == {"A": True, "B": False}

    def test_cannot_unset_the_default_directly(self, s2):
        a = make_calendar(s2, "A")
        r = call(s2["user"], "patch", s2, f"time/holiday-calendars/{a['id']}/", {"is_default": False})
        assert r.status_code == 400 and code(r) == "default_required"
        assert HolidayCalendar.objects.get(pk=a["id"]).is_default is True

    def test_rename(self, s2):
        a = make_calendar(s2, "A")
        r = call(s2["user"], "patch", s2, f"time/holiday-calendars/{a['id']}/", {"name": "  Renamed "}, 200)
        assert r.data["name"] == "Renamed"

    def test_duplicate_name_is_refused(self, s2):
        make_calendar(s2, "Canada")
        r = call(s2["user"], "post", s2, "time/holiday-calendars/", {"name": "canada"})
        assert r.status_code == 400 and code(r) == "duplicate_name"
        other = make_calendar(s2, "Other")
        r = call(s2["user"], "patch", s2, f"time/holiday-calendars/{other['id']}/", {"name": "Canada"})
        assert r.status_code == 400 and code(r) == "duplicate_name"
        # renaming to its own name is fine
        call(s2["user"], "patch", s2, f"time/holiday-calendars/{other['id']}/", {"name": "Other"}, 200)

    def test_a_deleted_calendars_name_can_be_reused(self, s2):
        make_calendar(s2, "Main")
        old = make_calendar(s2, "Old")
        call(s2["user"], "delete", s2, f"time/holiday-calendars/{old['id']}/", None, 204)
        assert make_calendar(s2, "Old")["name"] == "Old"

    def test_invalid_name_is_refused(self, s2):
        for body in ({}, {"name": ""}, {"name": "   "}, {"name": 5}, {"name": "x" * 101}):
            r = call(s2["user"], "post", s2, "time/holiday-calendars/", body)
            assert r.status_code == 400 and code(r) == "invalid_name", body

    def test_default_cannot_be_deleted_while_others_exist(self, s2):
        a = make_calendar(s2, "A")
        make_calendar(s2, "B")
        r = call(s2["user"], "delete", s2, f"time/holiday-calendars/{a['id']}/")
        assert r.status_code == 400 and code(r) == "default_calendar"
        assert HolidayCalendar.objects.filter(pk=a["id"]).exists()

    def test_only_calendar_can_be_deleted_then_the_next_is_default(self, s2):
        a = make_calendar(s2, "A")
        call(s2["user"], "delete", s2, f"time/holiday-calendars/{a['id']}/", None, 204)
        assert call(s2["user"], "get", s2, "time/holiday-calendars/", None, 200).data == []
        assert make_calendar(s2, "Fresh", default=False)["is_default"] is True

    def test_delete_takes_holidays_and_assignments_and_people_fall_back(self, s2):
        main = make_calendar(s2, "Main")
        other = make_calendar(s2, "Other")
        add_holiday(s2, main, OCT[2], "Main day")
        add_holiday(s2, other, OCT[1], "Other day")
        m = make_user(s2["workspace"], 15, "M")
        assign(s2, m, other)
        assert one_person_expected(s2, m)["tiles"]["expected_minutes"] == 40 * H - DAY  # Other day
        call(s2["user"], "delete", s2, f"time/holiday-calendars/{other['id']}/", None, 204)
        assert not Holiday.objects.filter(calendar_id=other["id"]).exists()
        assert not MemberHolidayCalendar.objects.filter(workspace=s2["workspace"], user=m).exists()
        assert [c["name"] for c in call(s2["user"], "get", s2, "time/holiday-calendars/", None, 200).data] == ["Main"]
        # M now follows the default calendar: Main day (Fri 2) instead of Other day.
        data = one_person_expected(s2, m)
        assert data["tiles"]["expected_minutes"] == 40 * H - DAY
        assert [str(d["date"]) for d in data["user"]["days_off"]] == ["2026-10-02"]

    def test_unknown_calendar_is_404(self, s2):
        ghost = uuid.uuid4()
        assert call(s2["user"], "patch", s2, f"time/holiday-calendars/{ghost}/", {"name": "x"}).status_code == 404
        assert call(s2["user"], "delete", s2, f"time/holiday-calendars/{ghost}/").status_code == 404
        assert call(s2["user"], "get", s2, f"time/holiday-calendars/{ghost}/holidays/").status_code == 404
        assert (
            call(
                s2["user"], "post", s2, f"time/holiday-calendars/{ghost}/holidays/", {"date": "2026-10-01", "name": "x"}
            ).status_code
            == 404
        )

    def test_audit_rows(self, s2):
        a = make_calendar(s2, "A")
        b = make_calendar(s2, "B")
        call(s2["user"], "patch", s2, f"time/holiday-calendars/{b['id']}/", {"name": "B2", "is_default": True}, 200)
        call(s2["user"], "delete", s2, f"time/holiday-calendars/{a['id']}/", None, 204)
        rows = audits(s2, "holiday_calendar")
        # create A, create B, A demoted, B renamed+promoted, delete A
        assert [(r.action, r.target_id) for r in rows] == [
            ("create", uuid.UUID(a["id"])),
            ("create", uuid.UUID(b["id"])),
            ("update", uuid.UUID(a["id"])),
            ("update", uuid.UUID(b["id"])),
            ("delete", uuid.UUID(a["id"])),
        ]
        assert rows[0].actor_id == s2["user"].id
        assert rows[0].after == {"name": "A", "is_default": True}
        assert rows[3].before == {"name": "B", "is_default": False}
        assert rows[3].after == {"name": "B2", "is_default": True}
        assert rows[4].before["name"] == "A"


# ---------------------------------------------------------------------------
# holidays
# ---------------------------------------------------------------------------


class TestHolidays:
    def test_create_list_sorted_and_year_filter(self, s2):
        cal = make_calendar(s2)
        add_holiday(s2, cal, "2027-01-01", "New Year")
        add_holiday(s2, cal, "2026-12-25", "Christmas", "half")
        add_holiday(s2, cal, "2026-07-01", "Canada Day")
        url = f"time/holiday-calendars/{cal['id']}/holidays/"
        everything = call(s2["user"], "get", s2, url, None, 200).data
        assert [(str(h["date"]), h["name"], h["portion"]) for h in everything] == [
            ("2026-07-01", "Canada Day", "full"),
            ("2026-12-25", "Christmas", "half"),
            ("2027-01-01", "New Year", "full"),
        ]
        assert set(everything[0]) == {"id", "date", "name", "portion"}
        in_2026 = call(s2["user"], "get", s2, url + "?year=2026", None, 200).data
        assert [h["name"] for h in in_2026] == ["Canada Day", "Christmas"]
        assert call(s2["user"], "get", s2, url + "?year=abc").status_code == 400

    def test_portion_defaults_to_full(self, s2):
        cal = make_calendar(s2)
        r = call(
            s2["user"],
            "post",
            s2,
            f"time/holiday-calendars/{cal['id']}/holidays/",
            {"date": "2026-10-02", "name": "X"},
            201,
        )
        assert r.data["portion"] == "full"

    def test_duplicate_date_in_a_calendar_is_refused_but_other_calendars_may_share_it(self, s2):
        a = make_calendar(s2, "A")
        b = make_calendar(s2, "B")
        add_holiday(s2, a, OCT[2])
        r = call(
            s2["user"],
            "post",
            s2,
            f"time/holiday-calendars/{a['id']}/holidays/",
            {"date": "2026-10-02", "name": "Again"},
        )
        assert r.status_code == 400 and code(r) == "duplicate_date"
        add_holiday(s2, b, OCT[2])
        # moving a holiday onto an occupied date is refused too
        other = add_holiday(s2, a, OCT[1])
        r = call(
            s2["user"], "patch", s2, f"time/holiday-calendars/{a['id']}/holidays/{other['id']}/", {"date": "2026-10-02"}
        )
        assert r.status_code == 400 and code(r) == "duplicate_date"

    def test_validation(self, s2):
        cal = make_calendar(s2)
        url = f"time/holiday-calendars/{cal['id']}/holidays/"
        bad = [
            ({"name": "x"}, "invalid_date"),
            ({"date": "nope", "name": "x"}, "invalid_date"),
            ({"date": 20261002, "name": "x"}, "invalid_date"),
            ({"date": "2026-10-02"}, "invalid_name"),
            ({"date": "2026-10-02", "name": "x", "portion": "quarter"}, "invalid_portion"),
        ]
        for body, expected in bad:
            r = call(s2["user"], "post", s2, url, body)
            assert r.status_code == 400 and code(r) == expected, body

    def test_patch_and_delete(self, s2):
        cal = make_calendar(s2)
        hol = add_holiday(s2, cal, OCT[2], "Old")
        url = f"time/holiday-calendars/{cal['id']}/holidays/{hol['id']}/"
        r = call(s2["user"], "patch", s2, url, {"name": "New", "portion": "half", "date": "2026-10-06"}, 200)
        assert (r.data["name"], r.data["portion"], str(r.data["date"])) == ("New", "half", "2026-10-06")
        call(s2["user"], "delete", s2, url, None, 204)
        assert call(s2["user"], "get", s2, f"time/holiday-calendars/{cal['id']}/holidays/", None, 200).data == []
        assert call(s2["user"], "delete", s2, url).status_code == 404

    def test_holiday_of_another_calendar_is_not_reachable_through_this_one(self, s2):
        a = make_calendar(s2, "A")
        b = make_calendar(s2, "B")
        hol = add_holiday(s2, b, OCT[2])
        r = call(s2["user"], "patch", s2, f"time/holiday-calendars/{a['id']}/holidays/{hol['id']}/", {"name": "x"})
        assert r.status_code == 404
        assert (
            call(s2["user"], "delete", s2, f"time/holiday-calendars/{a['id']}/holidays/{hol['id']}/").status_code == 404
        )

    def test_audit_rows(self, s2):
        cal = make_calendar(s2)
        hol = add_holiday(s2, cal, OCT[2], "Day", "half")
        url = f"time/holiday-calendars/{cal['id']}/holidays/{hol['id']}/"
        call(s2["user"], "patch", s2, url, {"portion": "full"}, 200)
        call(s2["user"], "delete", s2, url, None, 204)
        rows = audits(s2, "holiday")
        assert [r.action for r in rows] == ["create", "update", "delete"]
        assert rows[0].after == {"calendar_id": cal["id"], "date": "2026-10-02", "name": "Day", "portion": "half"}
        assert rows[1].before["portion"] == "half" and rows[1].after["portion"] == "full"
        assert rows[2].before["name"] == "Day"
        assert all(r.target_id == uuid.UUID(hol["id"]) and r.actor_id == s2["user"].id for r in rows)


# ---------------------------------------------------------------------------
# calendar assignment
# ---------------------------------------------------------------------------


class TestAssignment:
    def test_put_assigns_and_null_clears_and_members_endpoint_reports_it(self, s2):
        make_calendar(s2, "Default")
        other = make_calendar(s2, "Other")
        m = make_user(s2["workspace"], 15, "M")

        def row():
            data = call(s2["user"], "get", s2, "time/members/", None, 200).data
            return next(x for x in data["members"] if x["user_id"] == str(m.id))

        assert row()["holiday_calendar_id"] is None
        r = assign(s2, m, other)
        assert r.data == {"user_id": str(m.id), "calendar_id": other["id"]}
        assert row()["holiday_calendar_id"] == other["id"]
        # re-assigning to the same calendar is fine, and so is switching
        assign(s2, m, other)
        assert MemberHolidayCalendar.objects.filter(workspace=s2["workspace"], user=m).count() == 1
        r = assign(s2, m, None)
        assert r.data == {"user_id": str(m.id), "calendar_id": None}
        assert row()["holiday_calendar_id"] is None
        assign(s2, m, None)  # clearing again is a no-op

    def test_validation(self, s2):
        cal = make_calendar(s2)
        m = make_user(s2["workspace"], 15, "M")
        guest = make_user(s2["workspace"], 5, "G")
        outsider = make_user(s2["workspace"], None, "Out")
        for who in (guest, outsider):
            r = call(s2["user"], "put", s2, f"time/members/{who.id}/holiday-calendar/", {"calendar_id": cal["id"]})
            assert r.status_code == 400 and code(r) == "not_a_member"
        for body in ({}, {"calendar_id": "nope"}, {"calendar_id": str(uuid.uuid4())}):
            r = call(s2["user"], "put", s2, f"time/members/{m.id}/holiday-calendar/", body)
            assert r.status_code == 400 and code(r) == "invalid_calendar", body

    def test_a_person_on_calendar_b_does_not_get_calendar_as_holiday(self, s2):
        a = make_calendar(s2, "A")  # default
        b = make_calendar(s2, "B")
        add_holiday(s2, a, OCT[1], "A-only")
        add_holiday(s2, b, OCT[2], "B-only")
        on_a = make_user(s2["workspace"], 15, "OnA")  # unassigned -> default A
        on_b = make_user(s2["workspace"], 15, "OnB")
        assign(s2, on_b, b)
        with freeze_time(AFTER_RANGE):
            data = report(s2["user"], s2, user="team")
        row_a, row_b = member_row(data, on_a), member_row(data, on_b)
        assert [(str(d["date"]), d["label"]) for d in row_a["days_off"]] == [("2026-10-01", "A-only")]
        assert [(str(d["date"]), d["label"]) for d in row_b["days_off"]] == [("2026-10-02", "B-only")]
        assert row_a["expected_minutes"] == row_b["expected_minutes"] == 40 * H - DAY

    def test_no_default_calendar_means_no_holidays_for_unassigned(self, s2):
        cal = make_calendar(s2, "Only")
        add_holiday(s2, cal, OCT[1])
        HolidayCalendar.objects.filter(pk=cal["id"]).update(is_default=False)
        m = make_user(s2["workspace"], 15, "M")
        assert one_person_expected(s2, m)["tiles"]["expected_minutes"] == 40 * H
        assign(s2, m, cal)
        assert one_person_expected(s2, m)["tiles"]["expected_minutes"] == 40 * H - DAY

    def test_audit_rows(self, s2):
        a = make_calendar(s2, "A")
        b = make_calendar(s2, "B")
        m = make_user(s2["workspace"], 15, "M")
        assign(s2, m, a)
        assign(s2, m, b)
        assign(s2, m, None)
        rows = audits(s2, "member_calendar")
        assert [r.action for r in rows] == ["create", "update", "delete"]
        assert rows[0].after == {"user_id": str(m.id), "calendar_id": a["id"]}
        assert rows[1].before["calendar_id"] == a["id"] and rows[1].after["calendar_id"] == b["id"]
        assert rows[2].before["calendar_id"] == b["id"]


# ---------------------------------------------------------------------------
# leave CRUD
# ---------------------------------------------------------------------------


class TestLeave:
    def test_create_and_shape(self, s2):
        m = make_user(s2["workspace"], 15, "Mem")
        r = add_leave(s2, m, OCT[5], OCT[7], note="Trip")
        assert set(r.data) == {
            "id",
            "user_id",
            "display_name",
            "start_date",
            "end_date",
            "portion",
            "note",
            "created_by",
            "created_at",
        }
        assert r.data["user_id"] == str(m.id) and r.data["display_name"] == m.display_name
        assert r.data["portion"] == "full" and r.data["note"] == "Trip"
        assert r.data["created_by"] == str(s2["user"].id)

    def test_admin_lists_everyone_sorted_newest_first_with_filters(self, s2):
        a = make_user(s2["workspace"], 15, "A")
        b = make_user(s2["workspace"], 15, "B")
        add_leave(s2, a, "2026-10-05", "2026-10-06")
        add_leave(s2, b, "2026-11-02", "2026-11-03")
        add_leave(s2, a, "2026-09-01")
        rows = call(s2["user"], "get", s2, "time/leave/", None, 200).data
        assert [str(r["start_date"]) for r in rows] == ["2026-11-02", "2026-10-05", "2026-09-01"]
        by_user = call(s2["user"], "get", s2, f"time/leave/?user_id={a.id}", None, 200).data
        assert [str(r["start_date"]) for r in by_user] == ["2026-10-05", "2026-09-01"]
        # overlap semantics: a row is in if it touches [from, to]
        in_range = call(s2["user"], "get", s2, "time/leave/?from=2026-10-06&to=2026-10-31", None, 200).data
        assert [str(r["start_date"]) for r in in_range] == ["2026-10-05"]
        assert call(s2["user"], "get", s2, "time/leave/?from=2026-10-07&to=2026-10-31", None, 200).data == []
        assert call(s2["user"], "get", s2, "time/leave/?from=bad").status_code == 400
        assert call(s2["user"], "get", s2, "time/leave/?user_id=bad").status_code == 400

    def test_member_reads_only_their_own(self, s2):
        a = make_user(s2["workspace"], 15, "A")
        b = make_user(s2["workspace"], 15, "B")
        add_leave(s2, a, OCT[5])
        add_leave(s2, b, OCT[6])
        mine = call(a, "get", s2, "time/leave/", None, 200).data
        assert [r["user_id"] for r in mine] == [str(a.id)]
        assert [r["user_id"] for r in call(a, "get", s2, f"time/leave/?user_id={a.id}", None, 200).data] == [str(a.id)]
        r = call(a, "get", s2, f"time/leave/?user_id={b.id}")
        assert r.status_code == 403
        assert call(a, "get", s2, f"time/leave/?user_id={uuid.uuid4()}").status_code == 403

    def test_validation_codes(self, s2):
        m = make_user(s2["workspace"], 15, "M")
        r = add_leave(s2, m, OCT[6], OCT[5], expect=400)
        assert code(r) == "invalid_range"
        r = add_leave(s2, m, OCT[5], OCT[6], portion="half", expect=400)
        assert code(r) == "half_day_range"
        add_leave(s2, m, OCT[5], OCT[6])
        for start, end in ((OCT[6], OCT[8]), (OCT[4], OCT[5]), (OCT[5], OCT[6]), (OCT[1], OCT[30])):
            r = add_leave(s2, m, start, end, expect=400)
            assert code(r) == "overlap", (start, end)
        add_leave(s2, m, OCT[7], OCT[8])  # adjacent is fine
        add_leave(s2, m, OCT[1], OCT[2])
        # someone else's leave on the same days is no overlap
        other = make_user(s2["workspace"], 15, "O")
        add_leave(s2, other, OCT[5], OCT[6])
        # portion / date / note shape
        assert code(add_leave(s2, m, OCT[20], portion="quarter", expect=400)) == "invalid_portion"
        r = call(
            s2["user"], "post", s2, "time/leave/", {"user_id": str(m.id), "start_date": "x", "end_date": "2026-10-20"}
        )
        assert r.status_code == 400 and code(r) == "invalid_date"
        assert code(add_leave(s2, m, OCT[21], note="x" * 501, expect=400)) == "invalid_note"

    def test_not_a_member(self, s2):
        guest = make_user(s2["workspace"], 5, "G")
        outsider = make_user(s2["workspace"], None, "Out")
        gone = make_user(s2["workspace"], 15, "Gone")
        from plane.db.models import WorkspaceMember

        WorkspaceMember.objects.filter(member=gone, workspace=s2["workspace"]).update(is_active=False)
        for who in (guest, outsider, gone):
            assert code(add_leave(s2, who, OCT[5], expect=400)) == "not_a_member"
        r = call(
            s2["user"],
            "post",
            s2,
            "time/leave/",
            {"user_id": "nope", "start_date": "2026-10-05", "end_date": "2026-10-05"},
        )
        assert r.status_code == 400 and code(r) == "not_a_member"
        r = call(s2["user"], "post", s2, "time/leave/", {"start_date": "2026-10-05", "end_date": "2026-10-05"})
        assert r.status_code == 400 and code(r) == "not_a_member"
        assert not MemberLeave.objects.filter(workspace=s2["workspace"]).exists()

    def test_patch_revalidates_and_excludes_itself_from_overlap(self, s2):
        m = make_user(s2["workspace"], 15, "M")
        first = add_leave(s2, m, OCT[5], OCT[6]).data
        second = add_leave(s2, m, OCT[8], OCT[9]).data
        url = f"time/leave/{first['id']}/"
        r = call(s2["user"], "patch", s2, url, {"end_date": "2026-10-07", "note": "longer"}, 200)
        assert str(r.data["end_date"]) == "2026-10-07" and r.data["note"] == "longer"
        r = call(s2["user"], "patch", s2, url, {"end_date": "2026-10-08"})
        assert r.status_code == 400 and code(r) == "overlap"
        r = call(s2["user"], "patch", s2, url, {"end_date": "2026-10-04"})
        assert r.status_code == 400 and code(r) == "invalid_range"
        r = call(s2["user"], "patch", s2, url, {"portion": "half"})  # 5..7 as half
        assert r.status_code == 400 and code(r) == "half_day_range"
        r = call(
            s2["user"], "patch", s2, f"time/leave/{second['id']}/", {"start_date": "2026-10-09", "portion": "half"}, 200
        )
        assert r.data["portion"] == "half" and str(r.data["start_date"]) == "2026-10-09"
        assert call(s2["user"], "patch", s2, f"time/leave/{uuid.uuid4()}/", {"note": "x"}).status_code == 404
        assert call(s2["user"], "delete", s2, f"time/leave/{uuid.uuid4()}/").status_code == 404

    def test_delete(self, s2):
        m = make_user(s2["workspace"], 15, "M")
        leave = add_leave(s2, m, OCT[5]).data
        call(s2["user"], "delete", s2, f"time/leave/{leave['id']}/", None, 204)
        assert call(s2["user"], "get", s2, "time/leave/", None, 200).data == []
        add_leave(s2, m, OCT[5])  # the dates are free again

    def test_audit_rows(self, s2):
        m = make_user(s2["workspace"], 15, "M")
        leave = add_leave(s2, m, OCT[5], note="a").data
        call(s2["user"], "patch", s2, f"time/leave/{leave['id']}/", {"note": "b"}, 200)
        call(s2["user"], "delete", s2, f"time/leave/{leave['id']}/", None, 204)
        rows = audits(s2, "leave")
        assert [r.action for r in rows] == ["create", "update", "delete"]
        assert rows[0].after == {
            "user_id": str(m.id),
            "start_date": "2026-10-05",
            "end_date": "2026-10-05",
            "portion": "full",
            "note": "a",
        }
        assert rows[1].before["note"] == "a" and rows[1].after["note"] == "b"
        assert rows[2].before["note"] == "b"
        assert all(r.target_id == uuid.UUID(leave["id"]) and r.actor_id == s2["user"].id for r in rows)


# ---------------------------------------------------------------------------
# expected hours
# ---------------------------------------------------------------------------


class TestExpectedReduction:
    """Range 1–7 Oct, one person, 40 h baseline (Thu 1, Fri 2, Mon 5, Tue 6, Wed 7)."""

    def _expected(self, s2, user):
        return one_person_expected(s2, user)["tiles"]["expected_minutes"]

    def test_full_holiday(self, s2):
        cal = make_calendar(s2)
        add_holiday(s2, cal, OCT[2], "Founders Day")
        m = make_user(s2["workspace"], 15, "M")
        data = one_person_expected(s2, m)
        assert data["tiles"]["expected_minutes"] == 40 * H - DAY
        assert data["tiles"]["off_minutes"] == DAY
        assert data["user"]["days_off"] == [
            {"date": OCT[2], "kind": "holiday", "portion": "full", "label": "Founders Day"}
        ]
        by_day = {str(p["date"]): p for p in data["series"]}
        assert by_day["2026-10-02"]["expected_minutes"] == 0
        assert [p["cumulative_expected_minutes"] for p in data["series"]] == [480, 480, 480, 480, 960, 1440, 1920]
        # a person's week row carries off_minutes too
        assert data["weeks"][0]["off_minutes"] == DAY

    def test_half_holiday(self, s2):
        cal = make_calendar(s2)
        add_holiday(s2, cal, OCT[2], "Half Day", "half")
        m = make_user(s2["workspace"], 15, "M")
        data = one_person_expected(s2, m)
        assert data["tiles"]["expected_minutes"] == 40 * H - DAY // 2
        assert data["tiles"]["off_minutes"] == DAY // 2
        assert data["user"]["days_off"] == [{"date": OCT[2], "kind": "holiday", "portion": "half", "label": "Half Day"}]

    def test_full_leave(self, s2):
        m = make_user(s2["workspace"], 15, "M")
        add_leave(s2, m, OCT[5], OCT[6], note="Wedding")
        data = one_person_expected(s2, m)
        assert data["tiles"]["expected_minutes"] == 40 * H - 2 * DAY
        assert data["tiles"]["off_minutes"] == 2 * DAY
        assert data["user"]["days_off"] == [
            {"date": OCT[5], "kind": "leave", "portion": "full", "label": "Wedding"},
            {"date": OCT[6], "kind": "leave", "portion": "full", "label": "Wedding"},
        ]

    def test_half_leave_without_a_note_is_labelled_leave(self, s2):
        m = make_user(s2["workspace"], 15, "M")
        add_leave(s2, m, OCT[5], portion="half")
        data = one_person_expected(s2, m)
        assert data["tiles"]["expected_minutes"] == 40 * H - DAY // 2
        assert data["user"]["days_off"] == [{"date": OCT[5], "kind": "leave", "portion": "half", "label": "Leave"}]

    def test_holiday_plus_half_leave_is_capped_at_a_full_day(self, s2):
        cal = make_calendar(s2)
        add_holiday(s2, cal, OCT[2], "Fri Off")
        add_holiday(s2, cal, OCT[5], "Mon Half", "half")
        m = make_user(s2["workspace"], 15, "M")
        add_leave(s2, m, OCT[2], portion="half")  # full holiday + half leave -> capped at 1
        add_leave(s2, m, OCT[5], portion="half")  # half holiday + half leave -> exactly 1
        add_leave(s2, m, OCT[6], portion="half", note="Dentist")  # half leave alone
        add_holiday(s2, cal, OCT[7], "Wed Half", "half")
        data = one_person_expected(s2, m)
        # Fri 0, Mon 0, Tue half, Wed half (holiday only)  -> 40h - 1 - 1 - 0.5 - 0.5 days
        assert data["tiles"]["expected_minutes"] == 40 * H - 3 * DAY
        assert data["tiles"]["off_minutes"] == 3 * DAY
        assert data["user"]["days_off"] == [
            {"date": OCT[2], "kind": "both", "portion": "full", "label": "Fri Off + leave"},
            {"date": OCT[5], "kind": "both", "portion": "full", "label": "Mon Half + leave"},
            {"date": OCT[6], "kind": "leave", "portion": "half", "label": "Dentist"},
            {"date": OCT[7], "kind": "holiday", "portion": "half", "label": "Wed Half"},
        ]

    def test_two_halves_of_one_day_never_exceed_the_day(self, s2):
        cal = make_calendar(s2)
        add_holiday(s2, cal, OCT[2], "Half", "half")
        m = make_user(s2["workspace"], 15, "M")
        add_leave(s2, m, OCT[1], OCT[2])  # full leave on top of a half holiday
        data = one_person_expected(s2, m)
        assert data["tiles"]["expected_minutes"] == 40 * H - 2 * DAY
        assert data["tiles"]["off_minutes"] == 2 * DAY

    def test_holiday_or_leave_on_a_weekend_changes_nothing(self, s2):
        cal = make_calendar(s2)
        add_holiday(s2, cal, OCT[3], "Saturday holiday")
        m = make_user(s2["workspace"], 15, "M")
        add_leave(s2, m, OCT[3], OCT[4])
        data = one_person_expected(s2, m)
        assert data["tiles"]["expected_minutes"] == 40 * H
        assert data["tiles"]["off_minutes"] == 0
        assert data["user"]["days_off"] == []

    def test_a_leave_spanning_a_weekend_only_takes_working_days(self, s2):
        m = make_user(s2["workspace"], 15, "M")
        add_leave(s2, m, OCT[2], OCT[5])  # Fri, Sat, Sun, Mon
        data = one_person_expected(s2, m)
        assert data["tiles"]["expected_minutes"] == 40 * H - 2 * DAY
        assert [str(d["date"]) for d in data["user"]["days_off"]] == ["2026-10-02", "2026-10-05"]

    def test_untracked_day_is_unchanged(self, s2):
        cal = make_calendar(s2)
        add_holiday(s2, cal, OCT[6], "Tue Holiday")
        m = make_user(s2["workspace"], 15, "M")
        set_tracking(s2, m, False, OCT[6])  # not tracked from Tue on
        add_leave(s2, m, OCT[7])
        data = one_person_expected(s2, m)
        assert data["tiles"]["expected_minutes"] == 3 * DAY  # Thu, Fri, Mon
        assert data["tiles"]["off_minutes"] == 0
        assert data["user"]["days_off"] == []

    def test_time_logged_on_a_day_off_still_counts_in_logged(self, s2):
        cal = make_calendar(s2)
        add_holiday(s2, cal, OCT[2])
        m = make_user(s2["workspace"], 15, "M")
        log(s2, m, OCT[2], 3 * H)
        data = one_person_expected(s2, m)
        assert data["tiles"]["logged_minutes"] == 3 * H
        assert data["tiles"]["balance_minutes"] == 3 * H - (40 * H - DAY)

    def test_capacity_override_scales_the_reduction(self, s2):
        from .time_tracking_s5_support import set_capacity

        cal = make_calendar(s2)
        add_holiday(s2, cal, OCT[2])
        m = make_user(s2["workspace"], 15, "M")
        set_capacity(s2, m, 20 * H)  # 4 h a day
        data = one_person_expected(s2, m)
        assert data["tiles"]["expected_minutes"] == 16 * H
        assert data["tiles"]["off_minutes"] == 4 * H

    def test_team_tiles_and_member_rows(self, s2):
        cal = make_calendar(s2)
        add_holiday(s2, cal, OCT[2], "Fri")  # everyone (default calendar)
        a = make_user(s2["workspace"], 15, "A")
        b = make_user(s2["workspace"], 15, "B")
        add_leave(s2, a, OCT[5], portion="half")
        with freeze_time(AFTER_RANGE):
            data = report(s2["user"], s2, user="team")
        assert data["tiles"]["member_count"] == 3
        assert data["tiles"]["expected_minutes"] == 3 * (40 * H - DAY) - DAY // 2
        assert data["tiles"]["off_minutes"] == 3 * DAY + DAY // 2
        row_a, row_b = member_row(data, a), member_row(data, b)
        assert row_a["off_minutes"] == DAY + DAY // 2 and row_b["off_minutes"] == DAY
        assert [d["kind"] for d in row_a["days_off"]] == ["holiday", "leave"]
        assert [d["kind"] for d in row_b["days_off"]] == ["holiday"]
        by_day = {str(p["date"]): p for p in data["series"]}
        assert by_day["2026-10-02"]["expected_minutes"] == 0
        assert by_day["2026-10-05"]["expected_minutes"] == 3 * DAY - DAY // 2

    def test_to_date_figures_are_reduced_too(self, s2):
        cal = make_calendar(s2)
        add_holiday(s2, cal, OCT[2])
        m = make_user(s2["workspace"], 15, "M")
        with freeze_time(AFTER_RANGE):
            data = report(m, s2, user=m.id)  # a member sees their own report
        assert data["tiles"]["expected_to_date_minutes"] == data["tiles"]["expected_minutes"] == 40 * H - DAY

    def test_member_sees_own_days_off_in_the_report(self, s2):
        m = make_user(s2["workspace"], 15, "M")
        add_leave(s2, m, OCT[5], note="Away")
        with freeze_time(AFTER_RANGE):
            data = report(m, s2, user="me")
        assert data["tiles"]["off_minutes"] == DAY
        assert [d["label"] for d in data["user"]["days_off"]] == ["Away"]

    def test_query_count_does_not_grow_with_people(self, s2):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from plane.utils.time_tracking.days_off import days_off_many

        cal = make_calendar(s2)
        add_holiday(s2, cal, OCT[2])
        users = [make_user(s2["workspace"], 15, f"U{i}") for i in range(12)]
        for u in users[:6]:
            add_leave(s2, u, OCT[5], OCT[6])
        with CaptureQueriesContext(connection) as few:
            days_off_many(s2["workspace"].id, [u.id for u in users[:2]], OCT[1], OCT[7])
        with CaptureQueriesContext(connection) as many:
            result = days_off_many(s2["workspace"].id, [u.id for u in users], OCT[1], OCT[7])
        assert len(many) == len(few) <= 4
        assert set(result[users[0].id]) == {OCT[2], OCT[5], OCT[6]}
        assert set(result[users[11].id]) == {OCT[2]}


class TestTimesheetAndCsv:
    def test_timesheet_week_expected_and_off(self, s2):
        cal = make_calendar(s2)
        add_holiday(s2, cal, OCT[6], "Tuesday Holiday")
        m = make_user(s2["workspace"], 15, "M")
        add_leave(s2, m, OCT[7], portion="half", note="Appointment")
        add_leave(s2, m, OCT[3])  # Saturday leave: not a day off
        with freeze_time(AFTER_RANGE):
            r = client_for(m).get(ws_url(s2, "time/timesheet/?week_start=2026-10-05"))
        assert r.status_code == 200, r.content
        days = {str(d["date"]): d for d in r.data["days"]}
        assert days["2026-10-05"]["expected_minutes"] == DAY and days["2026-10-05"]["off"] is None
        assert days["2026-10-06"]["expected_minutes"] == 0
        assert days["2026-10-06"]["off"] == {
            "date": OCT[6],
            "kind": "holiday",
            "portion": "full",
            "label": "Tuesday Holiday",
        }
        assert days["2026-10-07"]["expected_minutes"] == DAY // 2
        assert days["2026-10-07"]["off"]["kind"] == "leave" and days["2026-10-07"]["off"]["portion"] == "half"
        assert days["2026-10-10"]["off"] is None and days["2026-10-10"]["expected_minutes"] == 0
        assert r.data["totals"]["expected"] == 5 * DAY - DAY - DAY // 2
        assert r.data["totals"]["expected_to_date"] == r.data["totals"]["expected"]

    def test_timesheet_week_with_leave_on_the_previous_week_is_clean(self, s2):
        m = make_user(s2["workspace"], 15, "M")
        add_leave(s2, m, OCT[1])
        with freeze_time(AFTER_RANGE):
            r = client_for(m).get(ws_url(s2, "time/timesheet/?week_start=2026-10-05"))
        assert r.status_code == 200
        assert all(d["off"] is None for d in r.data["days"])
        assert r.data["totals"]["expected"] == 5 * DAY

    def test_legacy_resource_csv_expected_column_is_reduced(self, s2):
        import csv as csv_module

        cal = make_calendar(s2)
        add_holiday(s2, cal, OCT[2])
        m = make_user(s2["workspace"], 15, "M")
        add_leave(s2, m, OCT[5])
        log(s2, m, OCT[1], 60)
        with freeze_time(AFTER_RANGE):
            url = ws_url(s2, "time-report/?group_by=resource&start_date=2026-10-01&end_date=2026-10-07")
            data = client_for(s2["user"]).get(url).data
            r = client_for(s2["user"]).get(url + "&export=csv")
        group = next(g for g in data["groups"] if g["key"] == str(m.id))
        assert group["expected_minutes"] == 40 * H - 2 * DAY
        rows = list(csv_module.reader(r.content.decode().splitlines()))
        header, row = rows[0], next(x for x in rows[1:] if x and x[0] == m.display_name)
        assert float(row[header.index("Expected (h)")]) == (40 * H - 2 * DAY) / 60

    def test_get_members_unaffected_apart_from_the_new_field(self, s2):
        make_calendar(s2)
        data = call(s2["user"], "get", s2, "time/members/", None, 200).data
        assert all("holiday_calendar_id" in m for m in data["members"])
