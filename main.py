from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import create_engine, text
from datetime import datetime
from uuid import uuid4
import os
from fastapi import Query
from notify import send_email_notification
import qrcode
import io
import zipfile
from fastapi.responses import Response
from fastapi import Request, Form
from fastapi.responses import RedirectResponse
from sqlalchemy import text
from datetime import date


app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key="your-super-secret-session-key")
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")
BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:3030")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
# Database
DATABASE_URL = "postgresql://postgres:Vaibhav@localhost:5432/AssetManagement"
engine = create_engine(DATABASE_URL)

def get_admin_session(request: Request):
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/admin/login", status_code=303)

@app.get("/admin/login", response_class=HTMLResponse)
def admin_login_form(request: Request):
    return templates.TemplateResponse("admin_login.html", {"request": request, "error": None})

@app.post("/admin/login")
def admin_login(request: Request, password: str = Form(...)):
    if password == ADMIN_PASSWORD:
        request.session["admin_logged_in"] = True
        return RedirectResponse(url="/admin/laptops", status_code=303)
    return templates.TemplateResponse("admin_login.html", {"request": request, "error": "Incorrect password"})

@app.get("/admin/logout")
def admin_logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/admin/login", status_code=303)

# ======================
# QR ENTRY ROUTE
# ======================
@app.get("/{asset_id}", response_class=HTMLResponse)
def asset_entry_page(request: Request, asset_id: str):
    with engine.connect() as conn:
        asset_result = conn.execute(
            text("SELECT * FROM laptop_details WHERE asset_id = :asset_id"),
            {"asset_id": asset_id}
        ).fetchone()

        if not asset_result:
            return HTMLResponse("Asset not found", status_code=404)

        asset = dict(asset_result._mapping)

        tickets = conn.execute(
            text("SELECT * FROM tickets WHERE asset_id = :asset_id ORDER BY time DESC"),
            {"asset_id": asset_id}
        ).fetchall()
        tickets = [dict(t._mapping) for t in tickets]

        ticket_ids = tuple(t["ticket_id"] for t in tickets)
        if ticket_ids:
            status_rows = conn.execute(
                text("SELECT * FROM ticket_status WHERE ticket_id IN :ids ORDER BY timestamp"),
                {"ids": ticket_ids}
            ).fetchall()
            status_map = {}
            for row in status_rows:
                status = dict(row._mapping)
                status_map.setdefault(status["ticket_id"], []).append(status)
            for ticket in tickets:
                ticket["status_history"] = status_map.get(ticket["ticket_id"], [])
                if isinstance(ticket["time"], str):
                    ticket["time"] = datetime.strptime(ticket["time"], "%Y-%m-%d %H:%M:%S")
        else:
            for ticket in tickets:
                ticket["status_history"] = []

    return templates.TemplateResponse("laptop_home.html", {
        "request": request,
        "laptop": asset,
        "tickets": tickets
    })


# ======================
# CREATE TICKET FORM
# ======================
@app.get("/create-ticket/{asset_id}", response_class=HTMLResponse)
def create_ticket_form(request: Request, asset_id: str):
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT * FROM laptop_details WHERE asset_id = :asset_id"),
            {"asset_id": asset_id}
        ).fetchone()

    if not result:
        return HTMLResponse("Asset not found", status_code=404)

    asset = dict(result._mapping)

    return templates.TemplateResponse("create_ticket.html", {
        "request": request,
        "asset_id": asset["asset_id"],
        "make": asset["make"],
        "model": asset["model"],
        "serial_number": asset["serial_number"]
    })


# ======================
# TICKET SUBMISSION
# ======================
@app.post("/submit-ticket")
def submit_ticket(
    name: str = Form(...),
    description: str = Form(...),
    asset_id: str = Form(...),
    make: str = Form(...),
    model: str = Form(...),
    serial_number: str = Form(...)
):
    ticket_id = f"TICKET-{uuid4().hex[:8].upper()}"
    now = datetime.now()

    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO tickets (ticket_id, asset_id, description, name, make, model, serial_number, time)
            VALUES (:ticket_id, :asset_id, :description, :name, :make, :model, :serial_number, :time)
        """), {
            "ticket_id": ticket_id,
            "asset_id": asset_id,
            "description": description,
            "name": name,
            "make": make,
            "model": model,
            "serial_number": serial_number,
            "time": now
        })

    try:
        send_email_notification({
            "ticket_id": ticket_id,
            "asset_id": asset_id,
            "description": description,
            "name": name,
            "brand": make,  # 👈 Still using 'brand' key for backward compatibility in email
            "model": model,
            "serial_number": serial_number
        })
    except Exception as e:
        print("❌ Email sending failed:", e)

    return RedirectResponse(url=f"/ticket/{ticket_id}", status_code=303)


# ======================
# USER DASHBOARD
# ======================
@app.get("/laptop/{asset_id}", response_class=HTMLResponse)
def asset_dashboard(request: Request, asset_id: str):
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT * FROM tickets WHERE asset_id = :asset_id"),
            {"asset_id": asset_id}
        )
        tickets = [dict(row._mapping) for row in result]

        ticket_ids = [t["ticket_id"] for t in tickets]
        all_status = []

        if ticket_ids:
            placeholders = ", ".join([f":id{i}" for i in range(len(ticket_ids))])
            query = text(f"""
                SELECT * FROM ticket_status
                WHERE ticket_id IN ({placeholders})
                ORDER BY timestamp
            """)
            params = {f"id{i}": ticket_ids[i] for i in range(len(ticket_ids))}
            all_status = conn.execute(query, params).fetchall()

    status_map = {}
    for row in all_status:
        status = dict(row._mapping)
        status_map.setdefault(status["ticket_id"], []).append(status)

    for ticket in tickets:
        ticket["status_history"] = status_map.get(ticket["ticket_id"], [])
        time_val = ticket.get("time")
        if isinstance(time_val, str):
            try:
                ticket["time"] = datetime.strptime(time_val, "%Y-%m-%d %H:%M:%S")
            except:
                ticket["time"] = None

    return templates.TemplateResponse("user_dashboard.html", {
        "request": request,
        "laptop_id": asset_id,
        "tickets": tickets
    })


# ======================
# TICKET DETAILS (User)
# ======================
@app.get("/ticket/{ticket_id}", response_class=HTMLResponse)
def ticket_details(request: Request, ticket_id: str, redirect: str = None):
    with engine.connect() as conn:
        ticket_result = conn.execute(
            text("SELECT * FROM tickets WHERE ticket_id = :ticket_id"),
            {"ticket_id": ticket_id}
        ).fetchone()

        status_result = conn.execute(
            text("SELECT * FROM ticket_status WHERE ticket_id = :ticket_id ORDER BY timestamp"),
            {"ticket_id": ticket_id}
        ).fetchall()

    if not ticket_result:
        return HTMLResponse("Ticket not found", status_code=404)

    ticket = dict(ticket_result._mapping)
    time_val = ticket.get("time")
    if isinstance(time_val, str):
        try:
            ticket["time"] = datetime.strptime(time_val, "%Y-%m-%d %H:%M:%S")
        except:
            ticket["time"] = None

    return templates.TemplateResponse("ticket_details.html", {
        "request": request,
        "ticket": ticket,
        "status_history": [dict(row._mapping) for row in status_result],
        "is_admin": False,
        "redirect": redirect
    })


@app.get("/admin/laptops", response_class=HTMLResponse)
def admin_laptops(request: Request):
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/admin/login", status_code=303)
    with engine.connect() as conn:
        # Total tickets
        total_tickets = conn.execute(text("SELECT COUNT(*) FROM tickets")).scalar()

        # Total tickets that were ever opened at least once
        open_tickets = conn.execute(text("""
            SELECT COUNT(DISTINCT ticket_id)
            FROM ticket_status
            WHERE status = 'Open'
        """)).scalar()

        # Tickets where latest status = 'Closed'
        closed_tickets = conn.execute(text("""
            SELECT COUNT(*) 
            FROM (
                SELECT ticket_id, status
                FROM ticket_status
                WHERE (ticket_id, timestamp) IN (
                    SELECT ticket_id, MAX(timestamp)
                    FROM ticket_status
                    GROUP BY ticket_id
                )
            ) latest
            WHERE status = 'Closed'
        """)).scalar()

        # Recent tickets (latest status + ticket info)
        recent_result = conn.execute(text("""
            SELECT t.*, ts.status 
            FROM tickets t
            JOIN (
                SELECT ticket_id, status
                FROM ticket_status
                WHERE (ticket_id, timestamp) IN (
                    SELECT ticket_id, MAX(timestamp)
                    FROM ticket_status
                    GROUP BY ticket_id
                )
            ) ts ON t.ticket_id = ts.ticket_id
            ORDER BY t.time DESC 
        """))
        recent_tickets = [dict(row._mapping) for row in recent_result]

        # Neutral tickets: tickets with NO Open or Closed status ever
        neutral_result = conn.execute(text("""
            SELECT * FROM tickets
            WHERE ticket_id NOT IN (
                SELECT ticket_id FROM ticket_status WHERE status IN ('Open', 'Closed')
            )
            ORDER BY time DESC
        """))
        neutral_tickets = [dict(row._mapping) for row in neutral_result]

        # Unique assets (from laptop_details for full inventory)
        asset_result = conn.execute(text("SELECT asset_id FROM laptop_details ORDER BY asset_id"))
        assets = [row.asset_id for row in asset_result]

    return templates.TemplateResponse("admin_laptops.html", {
        "request": request,
        "total_tickets": total_tickets,
        "open_tickets": open_tickets,
        "closed_tickets": closed_tickets,
        "recent_tickets": recent_tickets,
        "neutral_tickets": neutral_tickets,
        "assets": assets
    })

@app.get("/admin/tickets/download_csv")
def download_tickets_csv(request: Request):
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/admin/login", status_code=303)

    with engine.connect() as conn:
        # Get all tickets with their latest status
        result = conn.execute(text("""
            SELECT t.*, ts.status as current_status
            FROM tickets t
            LEFT JOIN (
                SELECT ticket_id, status
                FROM ticket_status
                WHERE (ticket_id, timestamp) IN (
                    SELECT ticket_id, MAX(timestamp)
                    FROM ticket_status
                    GROUP BY ticket_id
                )
            ) ts ON t.ticket_id = ts.ticket_id
            ORDER BY t.time DESC
        """))
        tickets = [dict(row._mapping) for row in result]

    # Create CSV output
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Write header
    writer.writerow([
        "Ticket ID", "Asset ID", "Name", "Description", 
        "Make", "Model", "Serial Number", "Time Created",
        "Current Status"
    ])
    
    # Write data
    for ticket in tickets:
        writer.writerow([
            ticket["ticket_id"],
            ticket["asset_id"],
            ticket["name"],
            ticket["description"],
            ticket["make"],
            ticket["model"],
            ticket["serial_number"],
            ticket["time"].strftime("%Y-%m-%d %H:%M:%S") if ticket["time"] else "",
            ticket.get("current_status", "")
        ])

    output.seek(0)
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=tickets.csv"}
    )

@app.get("/admin/ticket_counts")
def get_ticket_counts(request: Request):
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/admin/login", status_code=303)
    with engine.connect() as conn:
        # Total tickets
        total_tickets = conn.execute(text("SELECT COUNT(*) FROM tickets")).scalar()

        # Tickets ever marked as open
        open_tickets = conn.execute(text("""
            SELECT COUNT(DISTINCT ticket_id)
            FROM ticket_status
            WHERE status = 'Open'
        """)).scalar()

        # Tickets with latest status as closed
        closed_tickets = conn.execute(text("""
            SELECT COUNT(*) 
            FROM (
                SELECT ticket_id, status
                FROM ticket_status
                WHERE (ticket_id, timestamp) IN (
                    SELECT ticket_id, MAX(timestamp)
                    FROM ticket_status
                    GROUP BY ticket_id
                )
            ) latest
            WHERE status = 'Closed'
        """)).scalar()

        # Total number of assets
        total_assets = conn.execute(text("SELECT COUNT(*) FROM laptop_details")).scalar()

    return {
        "total": total_tickets,
        "open": open_tickets,
        "closed": closed_tickets,
        "total_assets": total_assets
    }

# ======================
# ADMIN: Ticket Editor
# ======================
@app.get("/admin/ticket/{ticket_id}", response_class=HTMLResponse)
def admin_ticket_details(request: Request, ticket_id: str):
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/admin/login", status_code=303)
    with engine.connect() as conn:
        ticket_result = conn.execute(
            text("SELECT * FROM tickets WHERE ticket_id = :ticket_id"),
            {"ticket_id": ticket_id}
        ).fetchone()

        status_result = conn.execute(
            text("SELECT * FROM ticket_status WHERE ticket_id = :ticket_id ORDER BY timestamp"),
            {"ticket_id": ticket_id}
        ).fetchall()

    if not ticket_result:
        return HTMLResponse("Ticket not found", status_code=404)

    return templates.TemplateResponse("admin_ticket_details.html", {
        "request": request,
        "ticket": dict(ticket_result._mapping),
        "status_history": [dict(row._mapping) for row in status_result],
        "statuses": ["Open", "Awaiting Inventory", "Under Repair", "Resolved", "Closed", "Rejected", "Asset Replacement"]
    })


# ======================
# ADMIN: Add Status
# ======================
@app.post("/admin/ticket/{ticket_id}/add_status")
def add_status(request: Request, ticket_id: str, status: str = Form(...), comment: str = Form(None)):
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/admin/login", status_code=303)
    now = datetime.now()
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO ticket_status (ticket_id, status, comment, timestamp)
            VALUES (:ticket_id, :status, :comment, :timestamp)
        """), {
            "ticket_id": ticket_id,
            "status": status,
            "comment": comment,
            "timestamp": now
        })
    return RedirectResponse(url=f"/admin/ticket/{ticket_id}?success=1", status_code=303)


# ======================
# ADMIN: Delete Status
# ======================
@app.post("/admin/ticket/{ticket_id}/delete_status")
def delete_status(request: Request, ticket_id: str, timestamp: str = Form(...)):
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/admin/login", status_code=303)
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM ticket_status WHERE ticket_id = :ticket_id AND timestamp = :timestamp"),
            {"ticket_id": ticket_id, "timestamp": timestamp}
        )
    return RedirectResponse(url=f"/admin/ticket/{ticket_id}", status_code=303)


# ======================
# USER: Add Comment
# ======================
@app.post("/ticket/{ticket_id}/add_comment")
def user_add_comment(request: Request, ticket_id: str, comment: str = Form(...)):
    now = datetime.now()
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO ticket_status (ticket_id, status, comment, timestamp)
            VALUES (:ticket_id, 'User Comment', :comment, :timestamp)
        """), {
            "ticket_id": ticket_id,
            "comment": comment,
            "timestamp": now
        })
    return RedirectResponse(url=f"/ticket/{ticket_id}", status_code=303)



@app.get("/admin/laptops/manage", response_class=HTMLResponse)
def manage_laptops(
    request: Request,
    query: str = Query(default="", alias="query"),
    filter_by: str = Query(default="asset_id", alias="filter_by")
):
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/admin/login", status_code=303)

    with engine.connect() as conn:
        # Allow search only on safe columns
        allowed_columns = {
            "asset_id", "serial_number", "make", "model", "email", "assigned_to"
        }

        if filter_by not in allowed_columns:
            filter_by = "asset_id"

        base_query = "SELECT * FROM laptop_details"
        
        # Add filter if search query is provided
        if query:
            base_query += f" WHERE {filter_by} ILIKE :query"
            result = conn.execute(text(base_query), {"query": f"%{query}%"})
        else:
            result = conn.execute(text(base_query))

        laptops = [dict(row._mapping) for row in result]

    return templates.TemplateResponse("manage_laptops.html", {
        "request": request,
        "laptops": laptops,
        "query": query,
        "filter_by": filter_by
    })



@app.post("/admin/laptops/add")
def add_laptop(
    request: Request,
    asset_id: str = Form(...),
    serial_number: str = Form(...),
    make: str = Form(...),
    model: str = Form(...),
    email: str = Form(...),
    assigned_to: str = Form(...),
    assign_date: str = Form(...),
    purchase_date: str = Form(...),
    invoice_no: str = Form(None),
    cost: str = Form(None),
    vendor: str = Form(None),
    charger: str = Form(None),
    processor: str = Form(None),
    graphics: str = Form(None),
    gpu: str = Form(None),
    ram: str = Form(None),
    disk: str = Form(None),
    part_replaced: str = Form(None),
    part_replacement_date: str = Form(None)
):
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/admin/login", status_code=303)

   
    if part_replacement_date == "":
        part_replacement_date = None
    if purchase_date == "":
        purchase_date = None
    if assign_date == "":
        assign_date = None

    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO laptop_details (
                asset_id, serial_number, make, model, email, assigned_to,
                assign_date, purchase_date, invoice_no, cost, vendor,
                charger, processor, graphics, gpu, ram, disk,
                part_replaced, part_replacement_date
            )
            VALUES (
                :asset_id, :serial_number, :make, :model, :email, :assigned_to,
                :assign_date, :purchase_date, :invoice_no, :cost, :vendor,
                :charger, :processor, :graphics, :gpu, :ram, :disk,
                :part_replaced, :part_replacement_date
            )
        """), {
            "asset_id": asset_id,
            "serial_number": serial_number,
            "make": make,
            "model": model,
            "email": email,
            "assigned_to": assigned_to,
            "assign_date": assign_date,
            "purchase_date": purchase_date,
            "invoice_no": invoice_no,
            "cost": cost,
            "vendor": vendor,
            "charger": charger,
            "processor": processor,
            "graphics": graphics,
            "gpu": gpu,
            "ram": ram,
            "disk": disk,
            "part_replaced": part_replaced,
            "part_replacement_date": part_replacement_date
        })

    return RedirectResponse(url="/admin/laptops/manage", status_code=303)

@app.post("/admin/laptops/delete")
def delete_laptop(request: Request, asset_id: str = Form(...)):
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/admin/login", status_code=303)

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM laptop_details WHERE asset_id = :asset_id"), {"asset_id": asset_id})

    return RedirectResponse(url="/admin/laptops/manage", status_code=303)

@app.post("/admin/laptops/edit/{asset_id}")
def inline_edit_laptop(
    request: Request,
    asset_id: str,
    serial_number: str = Form(...),
    make: str = Form(...),
    model: str = Form(...),
    email: str = Form(...),
    assigned_to: str = Form(...),
    assign_date: str = Form(None),
    purchase_date: str = Form(None)
):
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/admin/login", status_code=303)

    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE laptop_details
            SET serial_number = :serial_number,
                make = :make,
                model = :model,
                email = :email,
                assigned_to = :assigned_to,
                assign_date = :assign_date,
                purchase_date = :purchase_date
            WHERE asset_id = :asset_id
        """), {
            "serial_number": serial_number,
            "make": make,
            "model": model,
            "email": email,
            "assigned_to": assigned_to,
            "assign_date": assign_date or None,
            "purchase_date": purchase_date or None,
            "asset_id": asset_id
        })

    return RedirectResponse(url="/admin/laptops/manage", status_code=303)


@app.post("/admin/laptops/update/{asset_id}")
def update_laptop(
    request: Request,
    asset_id: str,
    serial_number: str = Form(...),
    make: str = Form(...),
    model: str = Form(...),
    email: str = Form(...),
    assigned_to: str = Form(...),
    assign_date: str = Form(...),
    purchase_date: str = Form(...),
    invoice_no: str = Form(None),
    cost: str = Form(None),
    vendor: str = Form(None),
    charger: str = Form(None),
    processor: str = Form(None),
    graphics: str = Form(None),
    gpu: str = Form(None),
    ram: str = Form(None),
    disk: str = Form(None),
    part_replaced: str = Form(None),
    part_replacement_date: str = Form(None),
):
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/admin/login", status_code=303)

    # Convert empty date strings to None
    assign_date = assign_date or None
    purchase_date = purchase_date or None
    part_replacement_date = part_replacement_date or None

    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE laptop_details SET
                serial_number = :serial_number,
                make = :make,
                model = :model,
                email = :email,
                assigned_to = :assigned_to,
                assign_date = :assign_date,
                purchase_date = :purchase_date,
                invoice_no = :invoice_no,
                cost = :cost,
                vendor = :vendor,
                charger = :charger,
                processor = :processor,
                graphics = :graphics,
                gpu = :gpu,
                ram = :ram,
                disk = :disk,
                part_replaced = :part_replaced,
                part_replacement_date = :part_replacement_date
            WHERE asset_id = :asset_id
        """), {
            "asset_id": asset_id,
            "serial_number": serial_number,
            "make": make,
            "model": model,
            "email": email,
            "assigned_to": assigned_to,
            "assign_date": assign_date,
            "purchase_date": purchase_date,
            "invoice_no": invoice_no,
            "cost": cost,
            "vendor": vendor,
            "charger": charger,
            "processor": processor,
            "graphics": graphics,
            "gpu": gpu,
            "ram": ram,
            "disk": disk,
            "part_replaced": part_replaced,
            "part_replacement_date": part_replacement_date
        })

    return RedirectResponse(url="/admin/laptops/manage", status_code=303)

@app.get("/admin/laptops/edit/{asset_id}", response_class=HTMLResponse)
def edit_laptop_form(request: Request, asset_id: str):
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/admin/login", status_code=303)

    with engine.connect() as conn:
        result = conn.execute(text("SELECT * FROM laptop_details WHERE asset_id = :asset_id"), {"asset_id": asset_id})
        laptop = result.fetchone()
        if not laptop:
            return HTMLResponse(content="Laptop not found", status_code=404)

    return templates.TemplateResponse("edit_laptop.html", {"request": request, "laptop": dict(laptop._mapping)})

from fastapi.responses import StreamingResponse
import csv
from io import StringIO
import zipfile

@app.get("/admin/laptops/download_csv")
def download_laptop_csv(request: Request):
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/admin/login", status_code=303)

    with engine.connect() as conn:
        result = conn.execute(text("SELECT * FROM laptop_details"))
        rows = result.fetchall()
        headers = result.keys()

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(row)

    output.seek(0)
    return StreamingResponse(output, media_type="text/csv", headers={
        "Content-Disposition": "attachment; filename=laptop_list.csv"
    })



@app.post("/admin/laptops/reassign")
def reassign_laptop(
    request: Request,
    asset_id: str = Form(...),
    assigned_to: str = Form(...),
    email: str = Form(...),
    reason: str = Form("Reassignment")
):
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/admin/login", status_code=303)

    now = datetime.now()
    changed_by = request.session.get("admin_username", "System")
    
    with engine.begin() as conn:
        # Get current assignment
        current = conn.execute(
            text("SELECT assigned_to, email FROM laptop_details WHERE asset_id = :asset_id"),
            {"asset_id": asset_id}
        ).fetchone()

        # Update laptop_details with new assignment
        conn.execute(
            text("""
                UPDATE laptop_details 
                SET assigned_to = :assigned_to, email = :email, assign_date = :now
                WHERE asset_id = :asset_id
            """), {
                "assigned_to": assigned_to,
                "email": email,
                "now": now,
                "asset_id": asset_id
            }
        )

        # Record in assignment history
        conn.execute(
            text("""
                INSERT INTO assignment_history (
                    asset_id, previous_assignee, new_assignee,
                    previous_email, new_email, change_date,
                    changed_by, reason
                ) VALUES (
                    :asset_id, :prev_assignee, :new_assignee,
                    :prev_email, :new_email, :change_date,
                    :changed_by, :reason
                )
            """), {
                "asset_id": asset_id,
                "prev_assignee": current.assigned_to if current else None,
                "new_assignee": assigned_to,
                "prev_email": current.email if current else None,
                "new_email": email,
                "change_date": now,
                "changed_by": changed_by,
                "reason": reason
            }
        )

    return RedirectResponse(url="/admin/laptops/manage", status_code=303)

from fastapi.responses import HTMLResponse
from sqlalchemy import text

@app.get("/admin/laptop/{asset_id}/history", response_class=HTMLResponse)
def view_assignment_history(request: Request, asset_id: str):
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/admin/login", status_code=303)

    with engine.connect() as conn:
        # Get current assignment
        laptop = conn.execute(
            text("SELECT * FROM laptop_details WHERE asset_id = :asset_id"),
            {"asset_id": asset_id}
        ).fetchone()

        # Get assignment history
        history = conn.execute(
            text("""
                SELECT * FROM assignment_history
                WHERE asset_id = :asset_id
                ORDER BY change_date DESC
            """), {"asset_id": asset_id}
        ).fetchall()

    return templates.TemplateResponse("assignment_history.html", {
        "request": request,
        "asset_id": asset_id,
        "current_assignment": dict(laptop._mapping) if laptop else None,
        "history": [dict(h._mapping) for h in history]
    })

import pandas as pd
from fastapi.responses import StreamingResponse

@app.get("/admin/laptop/{asset_id}/history/download")
def download_assignment_history(asset_id: str, request: Request):
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/admin/login", status_code=303)

    with engine.connect() as conn:
        # Get assignment history
        history = conn.execute(
            text("""
                SELECT 
                    asset_id,
                    previous_assignee AS "Previous Assignee",
                    new_assignee AS "New Assignee",
                    previous_email AS "Previous Email",
                    new_email AS "New Email",
                    change_date AS "Change Date",
                    changed_by AS "Changed By",
                    reason AS "Reason"
                FROM assignment_history
                WHERE asset_id = :asset_id
                ORDER BY change_date DESC
            """), {"asset_id": asset_id}
        ).fetchall()

        # Get current assignment
        current = conn.execute(
            text("""
                SELECT 
                    asset_id AS "Asset ID",
                    assigned_to AS "Current Assignee",
                    email AS "Current Email",
                    assign_date AS "Assigned Date"
                FROM laptop_details
                WHERE asset_id = :asset_id
            """), {"asset_id": asset_id}
        ).fetchone()

    # Create DataFrame
    df_history = pd.DataFrame(history)
    df_current = pd.DataFrame([dict(current._mapping)] if current else [])

    # Create Excel file in memory
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        if not df_current.empty:
            df_current.to_excel(writer, sheet_name='Current Assignment', index=False)
        df_history.to_excel(writer, sheet_name='Assignment History', index=False)
    
    output.seek(0)

    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename={asset_id}_assignment_history.xlsx"
        }
    )


from fastapi import FastAPI, Request
from fastapi.responses import Response, RedirectResponse, StreamingResponse
from sqlalchemy import create_engine, text
import qrcode
from PIL import Image, ImageDraw, ImageFont
import io, zipfile




# ----------------------------
# ✅ Single QR code endpoint
# ----------------------------
@app.get("/admin/laptops/qr/{asset_id}")
def download_single_qr(request: Request, asset_id: str):
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/admin/login", status_code=303)

    url = f"{BASE_URL}/{asset_id}"
    qr_img = qrcode.make(url).convert("RGB").resize((300, 300))

    width, height = qr_img.size
    new_height = height + 40
    result = Image.new("RGB", (width, new_height), "white")
    result.paste(qr_img, (0, 0))

    draw = ImageDraw.Draw(result)
    try:
        font = ImageFont.truetype("arial.ttf", 18)
    except:
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), asset_id, font=font)
    text_width = bbox[2] - bbox[0]
    draw.text(((width - text_width) / 2, height + 5), asset_id, fill="black", font=font)

    img_io = io.BytesIO()
    result.save(img_io, format="PNG")
    img_io.seek(0)

    headers = {"Content-Disposition": f"attachment; filename={asset_id}_qr.png"}
    return Response(content=img_io.read(), media_type="image/png", headers=headers)


# ----------------------------
# ✅ All QR codes zipped
# ----------------------------
@app.get("/admin/laptops/qr_all")
def download_all_qr_codes(request: Request):
    if not request.session.get("admin_logged_in"):
        return RedirectResponse(url="/admin/login", status_code=303)

    zip_buffer = io.BytesIO()

    with engine.connect() as conn:
        result = conn.execute(text("SELECT asset_id FROM laptop_details")).fetchall()
        asset_ids = [row.asset_id for row in result]

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
        for asset_id in asset_ids:
            qr_url = f"{BASE_URL}/{asset_id}"
            qr_img = qrcode.make(qr_url).convert("RGB").resize((300, 300))

            width, height = qr_img.size
            new_height = height + 40
            final_img = Image.new("RGB", (width, new_height), "white")
            final_img.paste(qr_img, (0, 0))

            draw = ImageDraw.Draw(final_img)
            try:
                font = ImageFont.truetype("arial.ttf", 18)
            except:
                font = ImageFont.load_default()

            bbox = draw.textbbox((0, 0), asset_id, font=font)
            text_width = bbox[2] - bbox[0]
            draw.text(((width - text_width) / 2, height + 5), asset_id, font=font, fill="black")

            img_bytes = io.BytesIO()
            final_img.save(img_bytes, format="PNG")
            img_bytes.seek(0)
            zipf.writestr(f"{asset_id}_qr.png", img_bytes.read())

    zip_buffer.seek(0)
    return StreamingResponse(zip_buffer, media_type="application/zip", headers={
        "Content-Disposition": "attachment; filename=all_qr_codes.zip"
    })
