"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError
load_dotenv()
from pydantic import BaseModel, Field
from decimal import Decimal, ROUND_HALF_UP
from calendar import monthrange


client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]
db.employees.create_index("emp_code", unique=True)
db.attendance_logs.create_index(
    [("emp_code", 1), ("date", 1)],
    unique=True
)

db.attendance_logs.create_index(
    [("date", -1), ("emp_code", 1)]
)

db.attendance_logs.create_index(
    [("emp_code", 1), ("date", 1), ("status", 1)]
)

db.employees.create_index(
    [("department", 1), ("joined_on", 1)]
)

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def compute_late_minutes(punch_in: datetime, shift_start: str) -> int:
    h, m = map(int, shift_start.split(":"))
    start = punch_in.replace(hour=h, minute=m, second=0, microsecond=0)
    minutes = int((punch_in - start).total_seconds() / 60)
    return minutes if minutes > 10 else 0



def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    elapsed_seconds = Decimal(
        str((punch_out - punch_in).total_seconds())
    )
    hours = elapsed_seconds / Decimal("3600")

    return float(
        hours.quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP
        )
    )




def compute_overtime(
    punch_out: datetime,
    shift_start: str,
    shift_end: str,
    date_str: str
) -> int:
    attendance_date = datetime.fromisoformat(date_str).date()

    start_hour, start_minute = map(int, shift_start.split(":"))
    end_hour, end_minute = map(int, shift_end.split(":"))

    shift_start_dt = datetime(
        attendance_date.year,
        attendance_date.month,
        attendance_date.day,
        start_hour,
        start_minute,
        tzinfo=IST
    )

    shift_end_dt = datetime(
        attendance_date.year,
        attendance_date.month,
        attendance_date.day,
        end_hour,
        end_minute,
        tzinfo=IST
    )

    # An overnight shift ends on the following day.
    if shift_end <= shift_start:
        shift_end_dt += timedelta(days=1)

    punch_out_ist = punch_out
    if punch_out_ist.tzinfo is None:
        punch_out_ist = punch_out_ist.replace(tzinfo=IST)
    else:
        punch_out_ist = punch_out_ist.astimezone(IST)

    minutes = int(
        (punch_out_ist - shift_end_dt).total_seconds() // 60
    )

    # Overtime counts only when at least 30 whole minutes.
    return minutes if minutes >= 30 else 0



# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
    status: str = "PRESENT"





class RegularizeIn(BaseModel):
    status: Optional[str] = None
    punch_in: Optional[int] = Field(
        default=None,
        ge=100000000000,
        le=4102444800000
    )
    punch_out: Optional[int] = Field(
        default=None,
        ge=100000000000,
        le=4102444800000
    )
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #

@app.get("/health")
def health():
    try:
        client.admin.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Database unavailable"
        )




@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc["created_at"] = datetime.now(timezone.utc)

    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="emp_code already exists"
        )

    doc.pop("_id", None)

    if isinstance(doc.get("created_at"), datetime):
        created_at = doc["created_at"]
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        doc["created_at"] = int(created_at.timestamp() * 1000)

    return doc



@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):

    q = {}
    if department:
        q["department"] = department

    skip = (page - 1) * page_size
    total = db.employees.count_documents(q)

    items = list(
        db.employees.find(q, {"_id": 0})
        .sort("emp_code", 1)
        .skip(skip)
        .limit(page_size)
    )

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size
    }


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    if body.status not in ("PRESENT", "WFH", "ON_DUTY"):
        raise HTTPException(
            status_code=422,
            detail="Status must be PRESENT, WFH, or ON_DUTY"
        )

    # Convert the timestamp to IST and discard fractional seconds.
    ts = (
        datetime.fromtimestamp(
            body.punched_at / 1000, tz=timezone.utc
        )
        if body.punched_at is not None
        else datetime.now(timezone.utc)
    ).astimezone(IST).replace(microsecond=0)

    shift_start = emp["shift_start"]
    shift_end = emp["shift_end"]

    # For overnight shifts, early-morning punches belong to
    # the shift that started on the previous calendar day.
    if shift_end <= shift_start and ts.strftime("%H:%M") < shift_end:
        attendance_date = (ts.date() - timedelta(days=1)).isoformat()
    else:
        attendance_date = ts.date().isoformat()

    # Calculate lateness relative to the correct shift start.
    punch_for_lateness = ts
    if attendance_date != ts.date().isoformat():
        punch_for_lateness = ts - timedelta(days=1)

    late_minutes = compute_late_minutes(
        punch_for_lateness, shift_start
    )

    doc = {
        "emp_code": body.emp_code,
        "date": attendance_date,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": late_minutes,
        "overtime_minutes": 0,
        "half_day": False,
        "history": []
    }

    try:
        # The unique compound index should be created at startup:
        # db.attendance_logs.create_index(
        #     [("emp_code", 1), ("date", 1)], unique=True
        # )
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="Already punched in for this attendance date"
        )

    # Return epoch milliseconds and do not expose MongoDB IDs.
    return {
        "emp_code": body.emp_code,
        "date": attendance_date,
        "status": body.status,
        "punch_in": int(ts.timestamp() * 1000),
        "punch_out": None,
        "work_hours": None,
        "late_minutes": late_minutes,
        "overtime_minutes": 0,
        "half_day": False,
        "history": []
    }



@app.post("/attendance/punch-out")
def punch_out(body: PunchInIn):
    from decimal import Decimal, ROUND_HALF_UP

    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    # Convert the supplied epoch milliseconds to IST.
    ts = datetime.fromtimestamp(
        body.punched_at / 1000, tz=timezone.utc
    ).astimezone(IST).replace(microsecond=0)

    # MongoDB stores datetimes as UTC; PyMongo may return them
    # as naive datetimes. Use UTC when querying MongoDB.
    ts_utc = ts.astimezone(timezone.utc).replace(tzinfo=None)

    # Find the latest open punch-in, including overnight shifts.
    record = db.attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "punch_out": None,
            "punch_in": {"$lte": ts_utc}
        },
        sort=[("punch_in", -1)]
    )

    if not record:
        # Distinguish an existing closed record from no punch-in.
        existing = db.attendance_logs.find_one(
            {"emp_code": body.emp_code},
            sort=[("punch_in", -1)]
        )
        if existing and existing.get("punch_out") is not None:
            raise HTTPException(
                status_code=409,
                detail="Already punched out"
            )
        raise HTTPException(
            status_code=404,
            detail="Punch-in record not found"
        )

    punch_in_time = record["punch_in"]

    # MongoDB's naive datetime represents UTC, not IST.
    if punch_in_time.tzinfo is None:
        punch_in_time = punch_in_time.replace(tzinfo=timezone.utc)

    punch_in_time = punch_in_time.astimezone(IST).replace(
        microsecond=0
    )

    elapsed_seconds = (ts - punch_in_time).total_seconds()

    if elapsed_seconds <= 0:
        raise HTTPException(
            status_code=422,
            detail="Punch-out must be after punch-in"
        )

    if elapsed_seconds > 24 * 60 * 60:
        raise HTTPException(
            status_code=422,
            detail="Punch-out cannot be more than 24 hours after punch-in"
        )

    # Calculate work hours using ROUND_HALF_UP.
    hours_decimal = (
        Decimal(str(elapsed_seconds)) / Decimal("3600")
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    work_hours = float(hours_decimal)

    # Use the original attendance date for overnight shifts.
    attendance_date = record["date"]

    late_minutes = compute_late_minutes(
        punch_in_time, emp["shift_start"]
    )

    overtime_minutes = compute_overtime(
        ts,
    emp["shift_start"],
    emp["shift_end"],
    attendance_date
    )

    # Overtime counts only when at least 30 whole minutes.
    if overtime_minutes < 30:
        overtime_minutes = 0

    half_day = work_hours < 4.50

    result = db.attendance_logs.update_one(
        {
            "_id": record["_id"],
            "punch_out": None
        },
        {
            "$set": {
                "punch_out": ts,
                "work_hours": work_hours,
                "late_minutes": late_minutes,
                "overtime_minutes": overtime_minutes,
                "half_day": half_day
            }
        }
    )

    if result.modified_count != 1:
        raise HTTPException(
            status_code=409,
            detail="Attendance record was already updated"
        )

    # Return timestamps as epoch milliseconds; don't expose MongoDB IDs.
    punch_in_ms = int(punch_in_time.timestamp() * 1000)
    punch_out_ms = int(ts.timestamp() * 1000)

    return {
        "emp_code": body.emp_code,
        "date": attendance_date,
        "status": record["status"],
        "punch_in": punch_in_ms,
        "punch_out": punch_out_ms,
        "work_hours": work_hours,
        "late_minutes": late_minutes,
        "overtime_minutes": overtime_minutes,
        "half_day": half_day,
        "history": record.get("history", [])
    }

@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(
    emp_code: str,
    date: str,
    body: RegularizeIn
):
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(
            status_code=404,
            detail="Employee not found"
        )

    record = db.attendance_logs.find_one({
        "emp_code": emp_code,
        "date": date
    })

    if not record:
        raise HTTPException(
            status_code=404,
            detail="Attendance record not found"
        )

    updates = {}

    if body.status is not None:
        if body.status not in (
            "PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"
        ):
            raise HTTPException(
                status_code=422,
                detail="Invalid status"
            )
        updates["status"] = body.status

    for field in ("punch_in", "punch_out"):
        value = getattr(body, field)

        if value is not None:
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 100000000000
                or value > 4102444800000
            ):
                raise HTTPException(
                    status_code=422,
                    detail=f"{field} must be epoch milliseconds"
                )

            updates[field] = (
                datetime.fromtimestamp(
                    value / 1000, tz=timezone.utc
                )
                .astimezone(IST)
                .replace(microsecond=0)
            )

    if body.status in ("ABSENT", "LEAVE"):
        if body.punch_in is not None or body.punch_out is not None:
            raise HTTPException(
                status_code=422,
                detail="ABSENT and LEAVE cannot include punch times"
            )

        updates["punch_in"] = None
        updates["punch_out"] = None

    final_status = updates.get("status", record["status"])
    final_punch_in = updates.get("punch_in", record.get("punch_in"))
    final_punch_out = updates.get("punch_out", record.get("punch_out"))

    # MongoDB may return naive datetimes; interpret them as IST.
    if final_punch_in is not None and final_punch_in.tzinfo is None:
        final_punch_in = final_punch_in.replace(tzinfo=IST)

    if final_punch_out is not None and final_punch_out.tzinfo is None:
        final_punch_out = final_punch_out.replace(tzinfo=IST)

    if final_status in ("ABSENT", "LEAVE"):
        final_punch_in = None
        final_punch_out = None
        updates["punch_in"] = None
        updates["punch_out"] = None

    if (
        final_status in ("PRESENT", "WFH", "ON_DUTY")
        and final_punch_in is None
    ):
        raise HTTPException(
            status_code=422,
            detail="Presence status requires punch_in"
        )

    if (
        final_punch_in is not None
        and final_punch_in.date().isoformat() != date
    ):
        raise HTTPException(
            status_code=422,
            detail="punch_in must fall on the attendance date"
        )

    if final_punch_out is not None:
        if final_punch_in is None or final_punch_out <= final_punch_in:
            raise HTTPException(
                status_code=422,
                detail="punch_out must be after punch_in"
            )

        if (final_punch_out - final_punch_in).total_seconds() > 86400:
            raise HTTPException(
                status_code=422,
                detail="punch_out cannot be more than 24 hours after punch_in"
            )

    # Record only fields that actually changed.
    changes = {}

    for field, value in updates.items():
        old_value = record.get(field)

        if field in ("punch_in", "punch_out") and old_value is not None:
            if old_value.tzinfo is None:
                old_value = old_value.replace(tzinfo=IST)

        if old_value != value:
            if field in ("punch_in", "punch_out"):
                old_ms = (
                    int(old_value.timestamp() * 1000)
                    if old_value is not None else None
                )
                new_ms = (
                    int(value.timestamp() * 1000)
                    if value is not None else None
                )
                changes[field] = {
                    "from": old_ms,
                    "to": new_ms
                }
            else:
                changes[field] = {
                    "from": old_value,
                    "to": value
                }

    if not changes:
        raise HTTPException(
            status_code=422,
            detail="Request does not change any fields"
        )

    # Recalculate derived fields using the final times.
    if final_punch_in is not None:
        late_minutes = compute_late_minutes(
            final_punch_in, emp["shift_start"]
        )
    else:
        late_minutes = 0

    if final_punch_in is not None and final_punch_out is not None:
        work_hours = compute_work_hours(
            final_punch_in, final_punch_out
        )
        overtime_minutes = compute_overtime(
            final_punch_out, emp["shift_end"], date
        )
        half_day = work_hours < 4.5
    else:
        work_hours = None
        overtime_minutes = 0
        half_day = False

    derived = {
        "late_minutes": late_minutes,
        "work_hours": work_hours,
        "overtime_minutes": overtime_minutes,
        "half_day": half_day
    }

    for field, value in derived.items():
        old_value = record.get(field)

        if old_value != value:
            changes[field] = {
                "from": old_value,
                "to": value
            }

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    history_entry = {
        "at": now_ms,
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes
    }

    update_fields = {**updates, **derived}

    # Compare the original history to avoid silently losing concurrent edits.
    result = db.attendance_logs.update_one(
        {
            "_id": record["_id"],
            "history": record.get("history", [])
        },
        {
            "$set": update_fields,
            "$push": {"history": history_entry}
        }
    )

    if result.modified_count != 1:
        raise HTTPException(
            status_code=409,
            detail="Attendance record changed concurrently; retry the correction"
        )

    updated = db.attendance_logs.find_one({
        "emp_code": emp_code,
        "date": date
    })

    updated.pop("_id", None)

    for field in ("punch_in", "punch_out"):
        if updated.get(field) is not None:
            updated[field] = int(
                updated[field].replace(tzinfo=IST).timestamp() * 1000
                if updated[field].tzinfo is None
                else updated[field].timestamp() * 1000
            )

    for entry in updated.get("history", []):
        if isinstance(entry.get("at"), datetime):
            at = entry["at"]
            if at.tzinfo is None:
                at = at.replace(tzinfo=timezone.utc)
            entry["at"] = int(at.timestamp() * 1000)

    return updated

@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    q = {}

    if emp_code:
        q["emp_code"] = emp_code

    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to

    if status:
        q["status"] = status

    total = db.attendance_logs.count_documents(q)
    skip = (page - 1) * page_size

    docs = list(
        db.attendance_logs.find(q, {"_id": 0})
        .sort([("date", -1), ("emp_code", 1)])
        .skip(skip)
        .limit(page_size)
    )

    return {
        "items": docs,
        "total": total,
        "page": page,
        "page_size": page_size
    }




@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str = Query(..., pattern=r"^\d{4}-\d{2}$")):
    emp = db.employees.find_one({"emp_code": emp_code})

    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    try:
        year, mon = map(int, month.split("-"))
        if mon < 1 or mon > 12:
            raise ValueError()
    except ValueError:
        raise HTTPException(status_code=422, detail="month must be YYYY-MM")

    month_start = f"{year:04d}-{mon:02d}-01"
    month_end = f"{year:04d}-{mon:02d}-{monthrange(year, mon)[1]:02d}"

    # Count weekdays from the later of joining date and month start.
    first_day = max(emp["joined_on"], month_start)
    working_days = 0

    if first_day <= month_end:
        from datetime import date as date_type
        current = date_type.fromisoformat(first_day)
        last = date_type.fromisoformat(month_end)

        while current <= last:
            if current.weekday() < 5:
                working_days += 1
            current += timedelta(days=1)

    # Calculate attendance metrics using MongoDB aggregation.
    pipeline = [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {"$gte": month_start, "$lte": month_end}
            }
        },
        {
            "$addFields": {
                "_parsed_date": {
                    "$dateFromString": {
                        "dateString": "$date",
                        "format": "%Y-%m-%d"
                    }
                }
            }
        },
        {
            "$addFields": {
                "_weekday": {
                    "$dayOfWeek": "$_parsed_date"
                }
            }
        },
        {
            "$group": {
                "_id": None,
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                    {"$in": ["$_weekday", [2, 3, 4, 5, 6]]}
                                ]
                            },
                            {
                                "$cond": [
                                    {"$eq": ["$half_day", True]},
                                    0.5,
                                    1
                                ]
                            },
                            0
                        ]
                    }
                },
                "leave_days": {
                    "$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}
                },
                "late_count": {
                    "$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}
                },
                "total_late_minutes": {"$sum": "$late_minutes"},
                "total_overtime_minutes": {"$sum": "$overtime_minutes"}
            }
        }
    ]

    result = list(db.attendance_logs.aggregate(pipeline))
    stats = result[0] if result else {}

    present_days = stats.get("present_days", 0)

    attendance_pct = None
    if working_days > 0:
        attendance_pct = float(
            (Decimal(str(present_days)) * Decimal("100") /
             Decimal(working_days)).quantize(
                Decimal("0.0001"), rounding=ROUND_HALF_UP
            )
        )

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": stats.get("leave_days", 0),
        "late_count": stats.get("late_count", 0),
        "total_late_minutes": stats.get("total_late_minutes", 0),
        "total_overtime_minutes": stats.get("total_overtime_minutes", 0),
        "attendance_pct": attendance_pct
    }

@app.get("/analytics/departments/summary")
def department_summary(
    month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    department: Optional[str] = None
):
    from calendar import monthrange
    from decimal import Decimal, ROUND_HALF_UP

    try:
        year, mon = map(int, month.split("-"))
        if not 1 <= mon <= 12:
            raise ValueError()
    except ValueError:
        raise HTTPException(status_code=422, detail="month must be YYYY-MM")

    month_start = f"{year:04d}-{mon:02d}-01"
    month_end = f"{year:04d}-{mon:02d}-{monthrange(year, mon)[1]:02d}"

    employee_match = {"joined_on": {"$lte": month_end}}
    if department:
        employee_match["department"] = department

    pipeline = [
        {"$match": employee_match},
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"code": "$emp_code"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {"$eq": ["$emp_code", "$$code"]},
                            "date": {"$gte": month_start, "$lte": month_end}
                        }
                    },
                    {
                        "$addFields": {
                            "_weekday": {
                                "$dayOfWeek": {
                                    "$dateFromString": {
                                        "dateString": "$date",
                                        "format": "%Y-%m-%d"
                                    }
                                }
                            }
                        }
                    }
                ],
                "as": "logs"
            }
        },
        {
            "$unwind": {
                "path": "$logs",
                "preserveNullAndEmptyArrays": True
            }
        },
        {
            "$group": {
                "_id": "$department",
                "headcount": {"$addToSet": "$emp_code"},
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$in": ["$logs.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                    {"$in": ["$logs._weekday", [2, 3, 4, 5, 6]]}
                                ]
                            },
                            {
                                "$cond": [
                                    {"$eq": ["$logs.half_day", True]},
                                    0.5,
                                    1
                                ]
                            },
                            0
                        ]
                    }
                },
                "work_hours_values": {
                    "$push": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$in": ["$logs.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                    {"$ne": [{"$ifNull": ["$logs.work_hours", None]}, None]}
                                ]
                            },
                            "$logs.work_hours",
                            None
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [{"$gt": ["$logs.late_minutes", 0]}, 1, 0]
                    }
                },
                "total_late_minutes": {
                    "$sum": {"$ifNull": ["$logs.late_minutes", 0]}
                },
                "leave_count": {
                    "$sum": {"$cond": [{"$eq": ["$logs.status", "LEAVE"]}, 1, 0]}
                },
                "on_duty_count": {
                    "$sum": {"$cond": [{"$eq": ["$logs.status", "ON_DUTY"]}, 1, 0]}
                }
            }
        },
        {"$sort": {"_id": 1}}
    ]

    rows = list(db.employees.aggregate(pipeline))
    output = []

    for row in rows:
        if not row.get("headcount"):
            continue

        hours = [
            value for value in row.get("work_hours_values", [])
            if value is not None
        ]
        avg_hours = None
        if hours:
            avg_hours = float(
                Decimal(str(sum(hours) / len(hours))).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
            )

        output.append({
            "department": row["_id"],
            "headcount": len(row["headcount"]),
            "present_days": row["present_days"],
            "avg_work_hours": avg_hours,
            "late_count": row["late_count"],
            "total_late_minutes": row["total_late_minutes"],
            "leave_count": row["leave_count"],
            "on_duty_count": row["on_duty_count"]
        })

    return {"month": month, "items": output}

@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    limit: int = Query(default=10, ge=1, le=100),
    department: Optional[str] = None
):
    from calendar import monthrange

    try:
        year, mon = map(int, month.split("-"))
        if not 1 <= mon <= 12:
            raise ValueError()
    except ValueError:
        raise HTTPException(status_code=422, detail="month must be YYYY-MM")

    month_start = f"{year:04d}-{mon:02d}-01"
    month_end = f"{year:04d}-{mon:02d}-{monthrange(year, mon)[1]:02d}"

    pipeline = [
        {
            "$match": {
                "date": {"$gte": month_start, "$lte": month_end},
                "late_minutes": {"$gt": 0}
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {"$sum": "$late_minutes"}
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "employee"
            }
        },
        {"$unwind": "$employee"}
    ]

    if department:
        pipeline.append({
            "$match": {"employee.department": department}
        })

    pipeline.extend([
        {
            "$project": {
                "_id": 0,
                "emp_code": "$_id",
                "name": "$employee.name",
                "department": "$employee.department",
                "total_late_minutes": 1
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {
                    "total_late_minutes": -1
                },
                "output": {
                    "rank": {"$rank": {}}
                }
            }
        },
        {"$match": {"rank": {"$lte": limit}}},
        {"$sort": {"total_late_minutes": -1, "emp_code": 1}}
    ])

    items = list(db.attendance_logs.aggregate(pipeline))

    return {
        "month": month,
        "items": items
    }


@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_date: str = Query(..., alias="from"),
    to_date: str = Query(..., alias="to")
):
    from datetime import date as date_type, datetime
    from decimal import Decimal, ROUND_HALF_UP

    # 1. Validate dates
    try:
        start = date_type.fromisoformat(from_date)
        end = date_type.fromisoformat(to_date)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="Dates must use YYYY-MM-DD format"
        )

    if start.isoformat() != from_date or end.isoformat() != to_date:
        raise HTTPException(
            status_code=422,
            detail="Dates must use YYYY-MM-DD format"
        )

    if end < start:
        raise HTTPException(
            status_code=422,
            detail="'to' must be on or after 'from'"
        )

    if (end - start).days + 1 > 92:
        raise HTTPException(
            status_code=422,
            detail="Date range cannot exceed 92 days"
        )

    # 2. Check department exists
    if not db.employees.find_one({"department": department}):
        raise HTTPException(
            status_code=404,
            detail="Department not found"
        )

    # $densify requires actual date values, not aggregation expressions.
    # Its upper bound is exclusive, so add one day to include to_date.
    start_dt = datetime.combine(start, datetime.min.time())
    end_dt = datetime.combine(end + timedelta(days=1), datetime.min.time())

    # 3. Aggregate attendance and generate a continuous daily series
    pipeline = [
        {
            "$match": {
                "department": department
            }
        },
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {
                    "code": "$emp_code",
                    "joined": "$joined_on"
                },
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$emp_code", "$$code"]},
                                    {"$gte": ["$date", from_date]},
                                    {"$lte": ["$date", to_date]},
                                    {"$gte": ["$date", "$$joined"]}
                                ]
                            }
                        }
                    },
                    {
                        "$project": {
                            "_id": 0,
                            "date": 1,
                            "status": 1,
                            "half_day": 1,
                            "late_minutes": 1
                        }
                    }
                ],
                "as": "logs"
            }
        },
        {
            "$unwind": {
                "path": "$logs",
                "preserveNullAndEmptyArrays": True
            }
        },
        {
            "$addFields": {
                "_date": {
                    "$dateFromString": {
                        "dateString": {
                            "$ifNull": ["$logs.date", from_date]
                        },
                        "format": "%Y-%m-%d"
                    }
                }
            }
        },
        {
            "$group": {
                "_id": "$_date",
                "present_count": {
                    "$sum": {
                        "$cond": [
                            {
                                "$in": [
                                    "$logs.status",
                                    ["PRESENT", "WFH", "ON_DUTY"]
                                ]
                            },
                            {
                                "$cond": [
                                    {"$eq": ["$logs.half_day", True]},
                                    0.5,
                                    1
                                ]
                            },
                            0
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": ["$logs.late_minutes", 0]},
                            1,
                            0
                        ]
                    }
                }
            }
        },
        {
            "$densify": {
                "field": "_id",
                "range": {
                    "step": 1,
                    "unit": "day",
                    "bounds": [start_dt, end_dt]
                }
            }
        },
        {
            "$set": {
                "present_count": {
                    "$ifNull": ["$present_count", 0]
                },
                "late_count": {
                    "$ifNull": ["$late_count", 0]
                }
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "let": {
                    "day": "$_id"
                },
                "pipeline": [
                    {
                        "$match": {
                            "department": department,
                            "$expr": {
                                "$lte": [
                                    "$joined_on",
                                    {
                                        "$dateToString": {
                                            "format": "%Y-%m-%d",
                                            "date": "$$day"
                                        }
                                    }
                                ]
                            }
                        }
                    },
                    {
                        "$count": "count"
                    }
                ],
                "as": "headcount_data"
            }
        },
        {
            "$addFields": {
                "headcount": {
                    "$ifNull": [
                        {"$arrayElemAt": ["$headcount_data.count", 0]},
                        0
                    ]
                },
                "is_working_day": {
                    "$in": [
                        {"$dayOfWeek": "$_id"},
                        [2, 3, 4, 5, 6]
                    ]
                }
            }
        },
        {
            "$addFields": {
                "attendance_rate": {
                    "$cond": [
                        {
                            "$and": [
                                "$is_working_day",
                                {"$gt": ["$headcount", 0]}
                            ]
                        },
                        {
                            "$multiply": [
                                {"$divide": ["$present_count", "$headcount"]},
                                100
                            ]
                        },
                        None
                    ]
                }
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {"_id": 1},
                "output": {
                    "moving_avg_7d": {
                        "$avg": "$attendance_rate",
                        "window": {
                            "documents": [-6, 0]
                        }
                    }
                }
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": {
                    "$dateToString": {
                        "format": "%Y-%m-%d",
                        "date": "$_id"
                    }
                },
                "headcount": 1,
                "present_count": 1,
                "late_count": 1,
                "is_working_day": 1,
                "attendance_rate": 1,
                "moving_avg_7d": 1
            }
        },
        {
            "$sort": {
                "date": 1
            }
        }
    ]

    rows = list(db.employees.aggregate(pipeline))

    # 4. Apply ROUND_HALF_UP to four decimal places
    def round_half_up(value):
        if value is None:
            return None
        return float(
            Decimal(str(value)).quantize(
                Decimal("0.0001"),
                rounding=ROUND_HALF_UP
            )
        )

    for row in rows:
        row["attendance_rate"] = round_half_up(
            row.get("attendance_rate")
        )
        row["moving_avg_7d"] = round_half_up(
            row.get("moving_avg_7d")
        )

    return {
        "department": department,
        "from": from_date,
        "to": to_date,
        "items": rows
    }



@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str,
    emp_code: Optional[str] = None,
    month: Optional[str] = None,
    department: Optional[str] = None,
    limit: int = Query(default=10, ge=1, le=50),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    from_date: Optional[str] = Query(default=None, alias="from"),
    to_date: Optional[str] = Query(default=None, alias="to"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    from datetime import datetime, timedelta

    allowed = {
        "attendance_list",
        "employee_monthly",
        "department_summary",
        "late_leaderboard",
        "department_trend",
    }

    if endpoint not in allowed:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported endpoint. Choose one of: {sorted(allowed)}",
        )

    def require_month():
        if not month:
            raise HTTPException(status_code=422, detail="month is required")

        try:
            if (
                len(month) != 7
                or month[4] != "-"
                or not month[:4].isdigit()
                or not month[5:].isdigit()
            ):
                raise ValueError

            year, mon = map(int, month.split("-"))
            if not 1 <= mon <= 12:
                raise ValueError

            first = f"{year:04d}-{mon:02d}-01"
            last = f"{year:04d}-{mon:02d}-{monthrange(year, mon)[1]:02d}"
            return first, last

        except ValueError:
            raise HTTPException(
                status_code=422,
                detail="month must use YYYY-MM format",
            )

    def explain_find(collection, query, sort=None, skip=0, take=None):
        cursor = collection.find(query)

        if sort:
            cursor = cursor.sort(sort)
        if skip:
            cursor = cursor.skip(skip)
        if take is not None:
            cursor = cursor.limit(take)

        return cursor.explain()

    def explain_aggregate(collection_name, pipeline):
        return db.command(
            "explain",
            {
                "aggregate": collection_name,
                "pipeline": pipeline,
                "cursor": {},
            },
            verbosity="executionStats",
        )

    if endpoint == "attendance_list":
        query = {}

        if emp_code:
            query["emp_code"] = emp_code

        if status:
            allowed_statuses = {
                "PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"
            }
            if status not in allowed_statuses:
                raise HTTPException(status_code=422, detail="Invalid status")
            query["status"] = status

        if date_from or date_to:
            query["date"] = {}
            if date_from:
                query["date"]["$gte"] = date_from
            if date_to:
                query["date"]["$lte"] = date_to

        plan = explain_find(
            db.attendance_logs,
            query,
            sort=[("date", -1), ("emp_code", 1)],
            skip=(page - 1) * page_size,
            take=page_size,
        )
        collection_name = "attendance_logs"

    elif endpoint == "employee_monthly":
        if not emp_code:
            raise HTTPException(
                status_code=422,
                detail="emp_code is required",
            )

        first, last = require_month()

        pipeline = [
            {
                "$match": {
                    "emp_code": emp_code,
                    "date": {"$gte": first, "$lte": last},
                }
            },
            {
                "$addFields": {
                    "_parsed_date": {
                        "$dateFromString": {
                            "dateString": "$date",
                            "format": "%Y-%m-%d",
                        }
                    }
                }
            },
            {
                "$addFields": {
                    "_weekday": {"$dayOfWeek": "$_parsed_date"}
                }
            },
        ]

        plan = explain_aggregate("attendance_logs", pipeline)
        collection_name = "attendance_logs"

    elif endpoint == "department_summary":
        first, last = require_month()

        employee_match = {"joined_on": {"$lte": last}}
        if department:
            employee_match["department"] = department

        pipeline = [
            {"$match": employee_match},
            {
                "$lookup": {
                    "from": "attendance_logs",
                    "let": {"code": "$emp_code"},
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {"$eq": ["$emp_code", "$$code"]},
                                "date": {"$gte": first, "$lte": last},
                            }
                        }
                    ],
                    "as": "logs",
                }
            },
            {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},
            {"$sort": {"department": 1}},
        ]

        plan = explain_aggregate("employees", pipeline)
        collection_name = "employees"

    elif endpoint == "late_leaderboard":
        first, last = require_month()

        match = {
            "date": {"$gte": first, "$lte": last},
            "late_minutes": {"$gt": 0},
        }

        pipeline = [
            {"$match": match},
            {
                "$group": {
                    "_id": "$emp_code",
                    "total_late_minutes": {"$sum": "$late_minutes"},
                    "late_count": {"$sum": 1},
                }
            },
            {
                "$setWindowFields": {
                    "sortBy": {"total_late_minutes": -1},
                    "output": {"rank": {"$rank": {}}},
                }
            },
            {"$match": {"rank": {"$lte": limit}}},
        ]

        plan = explain_aggregate("attendance_logs", pipeline)
        collection_name = "attendance_logs"

    else:  # department_trend
        if not department:
            raise HTTPException(
                status_code=422,
                detail="department is required",
            )

        if not from_date or not to_date:
            raise HTTPException(
                status_code=422,
                detail="from and to are required",
            )

        try:
            start = datetime.strptime(from_date, "%Y-%m-%d").date()
            end = datetime.strptime(to_date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail="from and to must use YYYY-MM-DD format",
            )

        if start.isoformat() != from_date or end.isoformat() != to_date:
            raise HTTPException(status_code=422, detail="Invalid date format")

        if end < start or (end - start).days + 1 > 92:
            raise HTTPException(status_code=422, detail="Invalid date range")

        if not db.employees.find_one({"department": department}):
            raise HTTPException(status_code=404, detail="Department not found")

        start_dt = datetime.combine(start, datetime.min.time())
        end_dt = datetime.combine(end + timedelta(days=1), datetime.min.time())

        pipeline = [
            {"$match": {"department": department}},
            {
                "$lookup": {
                    "from": "attendance_logs",
                    "let": {"code": "$emp_code", "joined": "$joined_on"},
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {
                                    "$and": [
                                        {"$eq": ["$emp_code", "$$code"]},
                                        {"$gte": ["$date", from_date]},
                                        {"$lte": ["$date", to_date]},
                                        {"$gte": ["$date", "$$joined"]},
                                    ]
                                }
                            }
                        },
                        {
                            "$project": {
                                "_id": 0,
                                "date": 1,
                                "status": 1,
                                "half_day": 1,
                                "late_minutes": 1,
                            }
                        },
                    ],
                    "as": "logs",
                }
            },
            {
                "$unwind": {
                    "path": "$logs",
                    "preserveNullAndEmptyArrays": True,
                }
            },
            {
                "$addFields": {
                    "_date": {
                        "$dateFromString": {
                            "dateString": {
                                "$ifNull": ["$logs.date", from_date]
                            },
                            "format": "%Y-%m-%d",
                        }
                    }
                }
            },
            {
                "$group": {
                    "_id": "$_date",
                    "present_count": {
                        "$sum": {
                            "$cond": [
                                {
                                    "$in": [
                                        "$logs.status",
                                        ["PRESENT", "WFH", "ON_DUTY"],
                                    ]
                                },
                                {
                                    "$cond": [
                                        {"$eq": ["$logs.half_day", True]},
                                        0.5,
                                        1,
                                    ]
                                },
                                0,
                            ]
                        }
                    },
                    "late_count": {
                        "$sum": {
                            "$cond": [
                                {"$gt": ["$logs.late_minutes", 0]},
                                1,
                                0,
                            ]
                        }
                    },
                }
            },
            {
                "$densify": {
                    "field": "_id",
                    "range": {
                        "step": 1,
                        "unit": "day",
                        "bounds": [start_dt, end_dt],
                    },
                }
            },
            {
                "$set": {
                    "present_count": {"$ifNull": ["$present_count", 0]},
                    "late_count": {"$ifNull": ["$late_count", 0]},
                }
            },
            {
                "$lookup": {
                    "from": "employees",
                    "let": {"day": "$_id"},
                    "pipeline": [
                        {
                            "$match": {
                                "department": department,
                                "$expr": {
                                    "$lte": [
                                        "$joined_on",
                                        {
                                            "$dateToString": {
                                                "format": "%Y-%m-%d",
                                                "date": "$$day",
                                            }
                                        },
                                    ]
                                },
                            }
                        },
                        {"$count": "count"},
                    ],
                    "as": "headcount_data",
                }
            },
            {
                "$addFields": {
                    "headcount": {
                        "$ifNull": [
                            {"$arrayElemAt": ["$headcount_data.count", 0]},
                            0,
                        ]
                    },
                    "is_working_day": {
                        "$in": [{"$dayOfWeek": "$_id"}, [2, 3, 4, 5, 6]]
                    },
                }
            },
            {
                "$addFields": {
                    "attendance_rate": {
                        "$cond": [
                            {
                                "$and": [
                                    "$is_working_day",
                                    {"$gt": ["$headcount", 0]},
                                ]
                            },
                            {
                                "$multiply": [
                                    {"$divide": ["$present_count", "$headcount"]},
                                    100,
                                ]
                            },
                            None,
                        ]
                    }
                }
            },
            {
                "$setWindowFields": {
                    "sortBy": {"_id": 1},
                    "output": {
                        "moving_avg_7d": {
                            "$avg": "$attendance_rate",
                            "window": {"documents": [-6, 0]},
                        }
                    },
                }
            },
        ]

        plan = explain_aggregate("employees", pipeline)
        collection_name = "employees"

    return {
        "endpoint": endpoint,
        "collection": collection_name,
        "explain": plan,
    }


# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #