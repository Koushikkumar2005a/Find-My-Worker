import os
from io import BytesIO
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import time
import random
import uuid
from datetime import datetime, timedelta, timezone
load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "fallback_dev_secret")
app.config['UPLOAD_FOLDER'] = 'static/uploads'

@app.context_processor
def inject_supabase_creds():
    return {
        'supabase_url': os.getenv("SUPABASE_URL"),
        'supabase_key': os.getenv("SUPABASE_KEY")
    }

# Initialize Supabase
supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY")
supabase = None
if supabase_url and supabase_key:
    try:
        from supabase import create_client, Client
        supabase: Client = create_client(supabase_url, supabase_key)
        print("Supabase connected.")
    except Exception as e:
        print(f"Failed to initialize supabase: {e}")

def is_user_locked(user_id):
    """Checks if a user has any jobs in 'payment_pending' status."""
    if not supabase:
        return False
    # Only users can be locked from booking/messaging
    try:
        resp = supabase.table("jobs").select("id").eq("user_id", user_id).eq("status", "payment_pending").execute()
        return len(resp.data) > 0
    except:
        return False

def upload_to_supabase(file, bucket="uploads"):
    """Helper to upload a file to Supabase Storage and return its public URL."""
    if not supabase:
        return None
    
    try:
        # Get file data and filename
        filename = secure_filename(f"{int(time.time())}_{file.filename}")
        file_content = file.read()
        
        # Upload to supabase
        supabase.storage.from_(bucket).upload(
            path=filename,
            file=file_content,
            file_options={"content-type": file.content_type}
        )
        
        # Get public URL
        url_resp = supabase.storage.from_(bucket).get_public_url(filename)
        return url_resp
    except Exception as e:
        print(f"Supabase upload error: {e}")
        return None

@app.route("/")
def home():
    if "user_id" in session:
        role = session.get("role")
        if role == "admin":
            return redirect(url_for("admin_dashboard"))
        return redirect(url_for("user_dashboard" if role == "user" else "worker_dashboard"))
    return render_template("index.html")

@app.route("/auth", methods=["GET", "POST"])
def auth():
    if not supabase:
        return render_template("auth.html", action="login", error="Database connection failed. Please check .env file.")
        
    if request.method == "POST":
        action = request.form.get("action")
        role = request.form.get("role")
        email = request.form.get("email")
        password = request.form.get("password")
        
        if action == "register":
            name = request.form.get("name")
            skills = request.form.get("skills", "") if role == "worker" else None
            phone_number = request.form.get("phone_number")
            
            # Check if user already exists
            existing_user = supabase.table("profiles").select("*").eq("email", email).execute()
            if len(existing_user.data) > 0:
                return render_template("auth.html", action="register", error="Email already exists")
                
            password_hash = generate_password_hash(password)
            
            profile_image_url = ""
            if 'profile_image' in request.files:
                file = request.files['profile_image']
                if file.filename != '':
                    profile_image_url = upload_to_supabase(file) or ""
            
            user_data = {
                "role": role,
                "name": name,
                "email": email,
                "password_hash": password_hash,
                "phone_number": phone_number,
                "profile_image": profile_image_url,
                "is_online": False,
                "earnings": 0 if role == "worker" else 0,
                "rating": 0 if role == "worker" else 0
            }
            if skills:
                user_data["skills"] = skills

            try:
                result = supabase.table("profiles").insert(user_data).execute()
                new_user = result.data[0]
                session["user_id"] = new_user["id"]
                session["role"] = new_user["role"]
                session["name"] = new_user["name"]
                return redirect(url_for("user_dashboard" if role == "user" else "worker_dashboard"))
            except Exception as e:
                 return render_template("auth.html", action="register", error=f"Registration failed: {str(e)}")

        elif action == "login":
            if email == "admin@fmw.com" and password == "Admin@1":
                session["user_id"] = "admin"
                session["role"] = "admin"
                session["name"] = "System Admin"
                return redirect(url_for("admin_dashboard"))
                
            role = request.form.get("role")
            response = supabase.table("profiles").select("*").eq("email", email).execute()
            
            if len(response.data) > 0:
                user = response.data[0]
                if check_password_hash(user["password_hash"], password) and user["role"] == role:
                    session["user_id"] = user["id"]
                    session["role"] = user["role"]
                    session["name"] = user["name"]
                    return redirect(url_for("user_dashboard" if user["role"] == "user" else "worker_dashboard"))
                else:
                    return render_template("auth.html", action="login", error="Invalid credentials")
            else:
                return render_template("auth.html", action="login", error="Invalid credentials")

    active_action = request.args.get("action", "login")
    return render_template("auth.html", action=active_action)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))

@app.route("/dashboard/admin")
def admin_dashboard():
    if session.get("role") != "admin":
        return redirect(url_for("home"))
    
    if not supabase:
        return "Database connection failed", 500
        
    profiles_resp = supabase.table("profiles").select("*").execute()
    profiles = profiles_resp.data
    
    jobs_resp = supabase.table("jobs").select("*").eq("status", "completed").execute()
    completed_jobs = jobs_resp.data
    
    total_users = len([p for p in profiles if p.get("role") == "user"])
    total_workers = len([p for p in profiles if p.get("role") == "worker"])
    
    total_gross = 0.0
    worker_earnings = {}
    
    for job in completed_jobs:
        price = float(job.get("quoted_price") or 0)
        total_gross += price
        worker_id = job.get("worker_id")
        if worker_id:
            worker_earnings[worker_id] = worker_earnings.get(worker_id, 0.0) + (price * 0.85)

    admin_earnings = total_gross * 0.15
    total_worker_net = total_gross * 0.85
    
    worker_stats = []
    for p in profiles:
        if p.get("role") == "worker":
            p_id = p.get("id")
            w_earnings = worker_earnings.get(p_id, 0.0)
            worker_stats.append({
                "id": p_id,
                "name": p.get("name", "Unknown"),
                "email": p.get("email", ""),
                "skills": p.get("skills", ""),
                "earnings": w_earnings
            })
            
    # Fetch reports and map names
    reports_resp = supabase.table("reports").select("*").order("created_at", desc=True).execute()
    reports = reports_resp.data
    
    if reports:
        u_ids = [r["user_id"] for r in reports]
        w_ids = [r["worker_id"] for r in reports]
        j_ids = [r["job_id"] for r in reports]
        
        # Profile lookup
        unique_p_ids = list(set(u_ids + w_ids))
        related_profiles = supabase.table("profiles").select("id, name").in_("id", unique_p_ids).execute()
        profile_map = {p["id"]: p["name"] for p in related_profiles.data}
        
        # Job lookup
        related_jobs = supabase.table("jobs").select("id, title").in_("id", list(set(j_ids))).execute()
        job_map = {j["id"]: j["title"] for j in related_jobs.data}
        
        for r in reports:
            r["user_name"] = profile_map.get(r["user_id"], "Unknown User")
            r["worker_name"] = profile_map.get(r["worker_id"], "Unknown Worker")
            r["job_title"] = job_map.get(r["job_id"], "Unknown Job")

    return render_template("admin_dashboard.html", 
        total_users=total_users, 
        total_workers=total_workers, 
        total_gross=total_gross, 
        admin_earnings=admin_earnings, 
        total_worker_net=total_worker_net,
        worker_stats=worker_stats,
        all_profiles=profiles,
        reports=reports)

@app.route("/api/admin/delete_profile", methods=["POST"])
def admin_delete_profile():
    if session.get("role") != "admin":
        return jsonify({"success": False, "error": "Unauthorized"}), 403
        
    data = request.json
    profile_id = data.get("profile_id")
    if not profile_id:
        return jsonify({"success": False, "error": "Missing profile ID"}), 400
        
    try:
        # Cascade deletion manually
        supabase.table("messages").delete().eq("sender_id", profile_id).execute()
        supabase.table("messages").delete().eq("receiver_id", profile_id).execute()
        
        supabase.table("reviews").delete().eq("worker_id", profile_id).execute()
        supabase.table("reviews").delete().eq("user_id", profile_id).execute()
        
        supabase.table("jobs").delete().eq("worker_id", profile_id).execute()
        supabase.table("jobs").delete().eq("user_id", profile_id).execute()
        
        supabase.table("profiles").delete().eq("id", profile_id).execute()
        
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/dashboard/user")
def user_dashboard():
    if session.get("role") != "user":
        return redirect(url_for("home"))
    
    user_id = session.get("user_id")
    
    # Handle Razorpay Payment Link Callback
    plink_id = request.args.get("razorpay_payment_link_id")
    plink_status = request.args.get("razorpay_payment_link_status")
    
    if plink_id and plink_status == "paid":
        try:
            # Find the payment record associated with this link
            pay_resp = supabase.table("payments").select("*").eq("razorpay_order_id", plink_id).execute()
            if pay_resp.data and pay_resp.data[0]["payment_status"] != "paid":
                job_id = pay_resp.data[0]["job_id"]
                # Mark as paid
                supabase.table("payments").update({"payment_status": "paid"}).eq("razorpay_order_id", plink_id).execute()
                # Mark job as completed
                supabase.table("jobs").update({"status": "completed"}).eq("id", job_id).execute()
                
                # Payout credit to worker
                worker_earnings = pay_resp.data[0]["worker_earnings"]
                job_info = supabase.table("jobs").select("worker_id").eq("id", job_id).execute()
                worker_id = job_info.data[0]["worker_id"]
                w_profile = supabase.table("profiles").select("earnings").eq("id", worker_id).execute()
                current_e = w_profile.data[0].get("earnings") or 0
                supabase.table("profiles").update({"earnings": current_e + worker_earnings}).eq("id", worker_id).execute()
        except:
            pass
    
    if not supabase:
        return "Database connection failed", 500
        
    workers_response = supabase.table("profiles").select("*").eq("role", "worker").execute()
    workers = workers_response.data
    
    # Fetch real user booking history
    jobs_response = supabase.table("jobs").select("*").eq("user_id", session.get("user_id")).order("created_at", desc=True).execute()
    past_jobs = jobs_response.data
    
    # Check if user is locked (any pending payments)
    is_locked = is_user_locked(session.get("user_id"))
    
    # Fetch extra works for active/pending jobs
    extra_works = []
    job_ids = [j["id"] for j in past_jobs]
    if job_ids:
        ew_resp = supabase.table("extra_work").select("*").in_("job_id", job_ids).execute()
        extra_works = ew_resp.data
    
    # Map worker names locally to avoid complex join syntax
    worker_lookup = {w["id"]: w["name"] for w in workers}
    
    # Split into active and history
    active_jobs = [j for j in past_jobs if j["status"] in ["pending", "quoted", "accepted", "in_progress", "payment_pending", "negotiating"]]
    history_jobs = [j for j in past_jobs if j["status"] in ["completed", "declined"]]
    
    # Check which history jobs are already rated
    rated_job_ids = []
    if history_jobs:
        reviews_resp = supabase.table("reviews").select("job_id").eq("user_id", session.get("user_id")).execute()
        rated_job_ids = [r["job_id"] for r in reviews_resp.data if r.get("job_id")]

    for j in active_jobs:
        j["worker_name"] = worker_lookup.get(j["worker_id"], "Unknown Worker")

    for j in history_jobs:
        j["worker_name"] = worker_lookup.get(j["worker_id"], "Unknown Worker")
        j["is_rated"] = j["id"] in rated_job_ids
        
    # Calculate review counts for each worker
    worker_ids = [w["id"] for w in workers]
    review_counts = {}
    if worker_ids:
        # Using a count select grouped by worker_id isn't directly available via simple select in Supabase SDK, 
        # so we fetch all review worker_ids and count them locally
        rev_data = supabase.table("reviews").select("worker_id").execute()
        for r in rev_data.data:
            wid = r["worker_id"]
            review_counts[wid] = review_counts.get(wid, 0) + 1

    for w in workers:
        w["review_count"] = review_counts.get(w["id"], 0)
        
    return render_template("user_dashboard.html", 
        workers=workers, 
        active_jobs=active_jobs, 
        history_jobs=history_jobs,
        extra_works=extra_works,
        is_locked=is_locked)

@app.route("/dashboard/worker")
def worker_dashboard():
    if session.get("role") != "worker":
        return redirect(url_for("home"))
        
    if not supabase:
         return "Database connection failed", 500
         
    response = supabase.table("profiles").select("*").eq("id", session.get("user_id")).execute()
    worker = response.data[0] if len(response.data) > 0 else None
    
    # Calculate real stats
    reviews_response = supabase.table("reviews").select("id", count="exact").eq("worker_id", session.get("user_id")).execute()
    total_reviews = reviews_response.count if reviews_response.count else 0
    
    jobs_response = supabase.table("jobs").select("*").eq("worker_id", session.get("user_id")).order("created_at", desc=True).execute()
    all_jobs = jobs_response.data
    
    user_ids = list(set([j["user_id"] for j in all_jobs]))
    if user_ids:
        users_response = supabase.table("profiles").select("id, name, phone_number").in_("id", user_ids).execute()
        user_lookup = {u["id"]: u["name"] for u in users_response.data}
        phone_lookup = {u["id"]: u["phone_number"] for u in users_response.data}
    else:
        user_lookup = {}
        phone_lookup = {}
        
    # Fetch extra works for worker's jobs
    extra_works = []
    job_ids = [j["id"] for j in all_jobs]
    if job_ids:
        ew_resp = supabase.table("extra_work").select("*").in_("job_id", job_ids).execute()
        extra_works = ew_resp.data
        
    for j in all_jobs:
        j["user_name"] = user_lookup.get(j["user_id"], "Unknown Customer")
        j["user_phone"] = phone_lookup.get(j["user_id"], "Not Provided")
        
    recent_requests = [j for j in all_jobs if j["status"] in ["pending", "quoted", "accepted", "in_progress", "payment_pending", "negotiating"]]
    history_jobs = [j for j in all_jobs if j["status"] in ["completed", "declined"]]
    active_jobs_count = len([j for j in recent_requests if j["status"] == "in_progress"])
    
    # Calculate weekly earnings growth
    now = datetime.now(timezone.utc)
    seven_days_ago = now - timedelta(days=7)
    fourteen_days_ago = now - timedelta(days=14)
    
    current_week_earnings = 0.0
    prev_week_earnings = 0.0
    lifetime_earnings = 0.0
    
    for j in all_jobs:
        if j["status"] == "completed" and j.get("created_at"):
            try:
                dt_str = j["created_at"].replace("Z", "+00:00")
                dt = datetime.fromisoformat(dt_str)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                price = float(j.get("quoted_price") or 0)
                net_price = price * 0.85 # Worker gets 85%
                lifetime_earnings += net_price
                
                if dt >= seven_days_ago:
                    current_week_earnings += net_price
                elif dt >= fourteen_days_ago:
                    prev_week_earnings += net_price
            except ValueError:
                pass
                
    if prev_week_earnings > 0:
        earnings_growth = ((current_week_earnings - prev_week_earnings) / prev_week_earnings) * 100.0
    elif current_week_earnings > 0:
        earnings_growth = 100.0
    else:
        earnings_growth = 0.0
    
    return render_template("worker_dashboard.html", 
        worker=worker, 
        total_reviews=total_reviews, 
        recent_requests=recent_requests, 
        history_jobs=history_jobs, 
        active_jobs_count=active_jobs_count, 
        earnings_growth=earnings_growth, 
        lifetime_earnings=lifetime_earnings,
        extra_works=extra_works)

@app.route("/api/reviews", methods=["POST"])
def submit_review():
    if session.get("role") != "user":
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    
    if not supabase:
        return jsonify({"success": False, "error": "Database error"}), 500
        
    data = request.json
    worker_id = int(data.get("worker_id"))
    job_id = int(data.get("job_id"))
    rating = float(data.get("rating"))
    description = data.get("description", "")
    
    # Verify job is completed and belongs to user
    job_check = supabase.table("jobs").select("status", "user_id").eq("id", job_id).execute()
    if not job_check.data or job_check.data[0]["status"] != "completed" or job_check.data[0]["user_id"] != session.get("user_id"):
        return jsonify({"success": False, "error": "Invalid job for review"}), 400

    # Check if already reviewed
    existing = supabase.table("reviews").select("id").eq("job_id", job_id).execute()
    if existing.data:
        return jsonify({"success": False, "error": "Job already reviewed"}), 400
        
    new_review = {
        "user_id": session.get("user_id"),
        "worker_id": worker_id,
        "job_id": job_id,
        "rating": rating,
        "description": description
    }
    supabase.table("reviews").insert(new_review).execute()
    
    # Recalculate worker rating
    reviews_response = supabase.table("reviews").select("rating").eq("worker_id", worker_id).execute()
    worker_reviews = [r["rating"] for r in reviews_response.data]
    
    if worker_reviews:
        # High precision calculation
        total_sum = sum([float(r) for r in worker_reviews])
        new_rating = round(total_sum / len(worker_reviews), 1)
        supabase.table("profiles").update({"rating": new_rating}).eq("id", worker_id).execute()
            
    return jsonify({"success": True})

@app.route("/api/report_worker", methods=["POST"])
def report_worker():
    if session.get("role") != "user":
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    
    if not supabase:
        return jsonify({"success": False, "error": "Database error"}), 500
        
    data = request.json
    worker_id = data.get("worker_id")
    job_id = data.get("job_id")
    reason = data.get("reason")
    description = data.get("description", "")
    
    if not all([worker_id, job_id, reason]):
        return jsonify({"success": False, "error": "Missing required fields"}), 400
        
    new_report = {
        "user_id": session.get("user_id"),
        "worker_id": worker_id,
        "job_id": job_id,
        "reason": reason,
        "description": description
    }
    
    try:
        supabase.table("reports").insert(new_report).execute()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/worker/toggle_status", methods=["POST"])
def toggle_status():
    if session.get("role") != "worker":
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    
    if not supabase:
        return jsonify({"success": False, "error": "Database error"}), 500
        
    data = request.json
    is_online = data.get("is_online", False)
    
    response = supabase.table("profiles").update({"is_online": is_online}).eq("id", session.get("user_id")).execute()
    
    if len(response.data) > 0:
        return jsonify({"success": True, "is_online": response.data[0]["is_online"]})
    
    return jsonify({"success": False, "error": "Worker not found"}), 404

@app.route("/api/book_worker", methods=["POST"])
def book_worker():
    if session.get("role") != "user":
        return jsonify({"success": False, "error": "Unauthorized"}), 403
        
    # Check if user is locked
    if is_user_locked(session.get("user_id")):
        return jsonify({"success": False, "error": "Your account is locked. Please complete your pending payment to unlock features."}), 403
        
    if not supabase:
        return jsonify({"success": False, "error": "Database error"}), 500
        
    worker_id = int(request.form.get("worker_id"))
    title = request.form.get("title", "Service Request")
    description = request.form.get("description", "")
    customer_lat = request.form.get("lat")
    customer_lng = request.form.get("lng")
    
    photo_url = None
    if 'photo' in request.files:
        file = request.files['photo']
        if file.filename != '':
            photo_url = upload_to_supabase(file)
    
    new_job = {
        "user_id": session.get("user_id"),
        "worker_id": worker_id,
        "title": title,
        "description": description,
        "photo_url": photo_url,
        "status": "pending",
        "customer_lat": float(customer_lat) if customer_lat else None,
        "customer_lng": float(customer_lng) if customer_lng else None
    }
    
    try:
        supabase.table("jobs").insert(new_job).execute()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/location/update", methods=["POST"])
def update_location():
    if session.get("role") != "worker" or not supabase:
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    
    data = request.json
    lat = data.get("lat")
    lng = data.get("lng")
    
    try:
        supabase.table("profiles").update({
            "live_lat": lat,
            "live_lng": lng,
            "last_location_update": time.time()
        }).eq("id", session.get("user_id")).execute()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/job/<int:job_id>/tracking")
def get_job_tracking(job_id):
    if not supabase:
        return jsonify({"success": False, "error": "Database error"}), 500
    
    try:
        # Get Job Location
        job_resp = supabase.table("jobs").select("customer_lat", "customer_lng", "worker_id", "status").eq("id", job_id).execute()
        if not job_resp.data:
            return jsonify({"success": False, "error": "Job not found"}), 404
        
        job = job_resp.data[0]
        
        # Get Worker Live Location
        worker_resp = supabase.table("profiles").select("live_lat", "live_lng", "name").eq("id", job["worker_id"]).execute()
        worker = worker_resp.data[0] if worker_resp.data else {}
        
        return jsonify({
            "success": True,
            "destination": {"lat": job["customer_lat"], "lng": job["customer_lng"]},
            "worker_pos": {"lat": worker.get("live_lat"), "lng": worker.get("live_lng")},
            "status": job["status"]
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/job/quote", methods=["POST"])
def quote_job():
    if session.get("role") != "worker":
        return jsonify({"success": False, "error": "Unauthorized"}), 403
        
    if not supabase:
        return jsonify({"success": False, "error": "Database error"}), 500
        
    data = request.json
    job_id = int(data.get("job_id"))
    price = float(data.get("price"))
    
    try:
        supabase.table("jobs").update({
            "status": "quoted",
            "quoted_price": price
        }).eq("id", job_id).eq("worker_id", session.get("user_id")).execute()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/job/status", methods=["POST"])
def update_job_status():
    if not supabase:
        return jsonify({"success": False, "error": "Database error"}), 500
        
    data = request.json
    job_id = int(data.get("job_id"))
    new_status = data.get("status") 
    
    user_id = session.get("user_id")
    role = session.get("role")
    
    try:
        # Check permissions
        job_resp = supabase.table("jobs").select("*").eq("id", job_id).execute()
        if not job_resp.data:
            return jsonify({"success": False, "error": "Job not found"}), 404
            
        job = job_resp.data[0]
        
        # Enforce App Lock for users
        if role == "user" and is_user_locked(user_id):
            return jsonify({"success": False, "error": "Account Locked. Payment Required."}), 403
        if role == "worker" and job["worker_id"] != user_id:
            return jsonify({"success": False, "error": "Unauthorized"}), 403
        if role == "user" and job["user_id"] != user_id:
            return jsonify({"success": False, "error": "Unauthorized"}), 403
            
        if role == "user" and new_status not in ["accepted", "declined", "negotiating"]:
             return jsonify({"success": False, "error": "Invalid user action"}), 400
             
        update_data = {"status": new_status}

        # Handle Negotiation Actions
        if new_status == "negotiating":
            update_data["bargain_price"] = data.get("price")
            update_data["bargain_by"] = role
        
        # User accepting counter-offer from worker
        if role == "user" and new_status == "accepted" and job["status"] == "negotiating":
             # This means user accepted a counter-offer, we update official price first
             update_data["quoted_price"] = job["bargain_price"]

        # User accepting quote -> Generate OTPs
        if role == "user" and new_status == "accepted":
            update_data["start_otp"] = f"{random.randint(100000, 999999)}"
            update_data["end_otp"] = f"{random.randint(100000, 999999)}"

        # Worker validating OTPs to start/complete job
        provided_otp = data.get("otp", "").strip()
        if role == "worker":
            if new_status == "in_progress":
                if job.get("start_otp") and provided_otp != job.get("start_otp"):
                    return jsonify({"success": False, "error": "Wrong OTP. Please check with customer."}), 400
            elif new_status == "payment_pending" and job["status"] == "in_progress":
                if job.get("end_otp") and provided_otp != job.get("end_otp"):
                    return jsonify({"success": False, "error": "Wrong OTP. Please check with customer."}), 400
                
                # We move to payment_pending. Earnings are updated ONLY after Razorpay verification.
                update_data["status"] = "payment_pending"
            
        supabase.table("jobs").update(update_data).eq("id", job_id).execute()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/profile/<int:profile_id>")
def view_profile(profile_id):
    if "user_id" not in session:
        return redirect(url_for("home"))
        
    if not supabase:
        return "Database connection failed", 500
        
    response = supabase.table("profiles").select("*").eq("id", profile_id).execute()
    if not response.data:
        return "Profile not found", 404
        
    profile = response.data[0]
    
    # If the profile is a worker, fetch their reviews
    reviews_data = []
    if profile["role"] == "worker":
        rev_resp = supabase.table("reviews").select("rating, description, created_at, user_id").eq("worker_id", profile_id).order("created_at", desc=True).execute()
        if rev_resp.data:
            user_ids = list(set([r["user_id"] for r in rev_resp.data]))
            users_resp = supabase.table("profiles").select("id, name").in_("id", user_ids).execute()
            user_map = {u["id"]: u["name"] for u in users_resp.data}
            for r in rev_resp.data:
                r["user_name"] = user_map.get(r["user_id"], "Unknown User")
        reviews_data = rev_resp.data
        
    is_owner = (session.get("user_id") == profile_id)
    
    # If a worker is viewing a user, or if a user is viewing their own profile
    user_jobs = []
    if profile["role"] == "user":
        if is_owner:
            # Fetch all jobs for this user
            jobs_resp = supabase.table("jobs").select("*").eq("user_id", profile_id).order("created_at", desc=True).execute()
            user_jobs = jobs_resp.data
        elif session.get("role") == "worker":
            # Fetch only jobs between this worker and this user
            jobs_resp = supabase.table("jobs").select("*").eq("user_id", profile_id).eq("worker_id", session.get("user_id")).order("created_at", desc=True).execute()
            user_jobs = jobs_resp.data
            
        # For user jobs, we also want the worker names if possible
        if user_jobs:
            worker_ids = list(set([j["worker_id"] for j in user_jobs]))
            if worker_ids:
                workers_resp = supabase.table("profiles").select("id, name").in_("id", worker_ids).execute()
                worker_map = {w["id"]: w["name"] for w in workers_resp.data}
                for j in user_jobs:
                    j["worker_name"] = worker_map.get(j["worker_id"], "Unknown Worker")
    
    # App Lock Check
    is_locked = False
    if session.get("user_id"):
        is_locked = is_user_locked(session.get("user_id"))
    
    return render_template("profile.html", profile=profile, reviews=reviews_data, user_jobs=user_jobs, is_owner=is_owner, is_locked=is_locked)

@app.route("/api/profile/update", methods=["POST"])
def update_profile():
    if "user_id" not in session:
        return jsonify({"success": False, "error": "Unauthorized"}), 403
        
    if not supabase:
        return jsonify({"success": False, "error": "Database error"}), 500
        
    # The frontend will now send FormData containing strings and potentially a file
    name = request.form.get("name")
    phone_number = request.form.get("phone_number")
    skills = request.form.get("skills")
    
    update_data = {}
    if name: update_data["name"] = name
    if phone_number is not None: update_data["phone_number"] = phone_number
    if skills is not None: update_data["skills"] = skills
    
    if 'profile_image' in request.files:
        file = request.files['profile_image']
        if file.filename != '':
            image_url = upload_to_supabase(file)
            if image_url:
                update_data["profile_image"] = image_url
            
    if not update_data:
        return jsonify({"success": False, "error": "No valid fields provided"}), 400
        
    try:
        supabase.table("profiles").update(update_data).eq("id", session.get("user_id")).execute()
        if "name" in update_data:
            session["name"] = update_data["name"]
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/keys/update", methods=["POST"])
def update_public_key():
    if "user_id" not in session:
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    if not supabase:
        return jsonify({"success": False, "error": "Database error"}), 500
    
    data = request.json
    public_key = data.get("public_key")
    if not public_key:
        return jsonify({"success": False, "error": "Missing public key"}), 400
        
    try:
        supabase.table("profiles").update({"public_key": public_key}).eq("id", session.get("user_id")).execute()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/keys/<int:target_user_id>", methods=["GET"])
def get_public_key(target_user_id):
    if "user_id" not in session:
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    if not supabase:
        return jsonify({"success": False, "error": "Database error"}), 500
        
    try:
        response = supabase.table("profiles").select("public_key").eq("id", target_user_id).execute()
        if len(response.data) > 0 and response.data[0].get("public_key"):
            return jsonify({"success": True, "public_key": response.data[0]["public_key"]})
        return jsonify({"success": False, "error": "Key not found"}), 404
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/chat/send", methods=["POST"])
def send_message():
    if "user_id" not in session:
        return jsonify({"success": False, "error": "Unauthorized"}), 403
        
    # Check if user is locked
    if is_user_locked(session.get("user_id")):
        return jsonify({"success": False, "error": "Your account is locked. Payment required to send messages."}), 403
    if not supabase:
        return jsonify({"success": False, "error": "Database error"}), 500
        
    data = request.json
    job_id = data.get("job_id")
    receiver_id = data.get("receiver_id")
    enc_for_receiver = data.get("enc_for_receiver")
    enc_for_sender = data.get("enc_for_sender")
    
    if not all([job_id, receiver_id, enc_for_receiver, enc_for_sender]):
        return jsonify({"success": False, "error": "Missing fields"}), 400
        
    # Optional: Verify that the current user is part of the job
    try:
        new_msg = {
            "job_id": job_id,
            "sender_id": session.get("user_id"),
            "receiver_id": receiver_id,
            "encrypted_content_for_receiver": enc_for_receiver,
            "encrypted_content_for_sender": enc_for_sender
        }
        supabase.table("messages").insert(new_msg).execute()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/extra_work/add", methods=["POST"])
def add_extra_work():
    if session.get("role") != "worker":
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    
    data = request.json
    job_id = data.get("job_id")
    description = data.get("description")
    amount = data.get("amount")
    
    if not all([job_id, description, amount]):
        return jsonify({"success": False, "error": "Missing fields"}), 400
        
    try:
        new_extra = {
            "job_id": job_id,
            "description": description,
            "amount": float(amount),
            "status": "pending"
        }
        supabase.table("extra_work").insert(new_extra).execute()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/extra_work/status", methods=["POST"])
def update_extra_work_status():
    if session.get("role") != "user":
        return jsonify({"success": False, "error": "Unauthorized"}), 403
        
    data = request.json
    extra_id = data.get("extra_id")
    new_status = data.get("status") # approved or rejected
    
    if new_status not in ["approved", "rejected"]:
        return jsonify({"success": False, "error": "Invalid status"}), 400
        
    try:
        supabase.table("extra_work").update({"status": new_status}).eq("id", extra_id).execute()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/payment/init-mock", methods=["POST"])
def init_mock_payment():
    if session.get("role") != "user":
        return jsonify({"success": False, "error": "Unauthorized"}), 403
        
    data = request.json
    job_id = data.get("job_id")
    
    try:
        # Calculate total
        job_resp = supabase.table("jobs").select("quoted_price").eq("id", job_id).execute()
        job = job_resp.data[0]
        base_amount = float(job.get("quoted_price") or 0)
        
        extras_resp = supabase.table("extra_work").select("amount").eq("job_id", job_id).eq("status", "approved").execute()
        extras_total = sum([float(e["amount"]) for e in extras_resp.data])
        
        total_amount = base_amount + extras_total
        commission = total_amount * 0.15
        worker_earnings = total_amount - commission
        
        pay_record = {
            "job_id": job_id,
            "total_amount": total_amount,
            "commission": commission,
            "worker_earnings": worker_earnings,
            "razorpay_order_id": f"mock_{int(time.time())}",
            "payment_status": "pending"
        }
        res = supabase.table("payments").insert(pay_record).execute()
        
        return jsonify({
            "success": True, 
            "total_amount": total_amount,
            "payment_id": res.data[0]["id"]
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/payment/simulate-success", methods=["POST"])
def simulate_payment():
    # Simulation is now the default flow
    data = request.json
    job_id = data.get("job_id")
    method = data.get("method", "UPI")
    
    try:
        pay_resp = supabase.table("payments").select("*").eq("job_id", job_id).eq("payment_status", "pending").execute()
        if not pay_resp.data:
            return jsonify({"success": False, "error": "No pending payment found"}), 404
            
        payment = pay_resp.data[0]
        supabase.table("payments").update({
            "payment_status": "paid", 
            "razorpay_payment_id": f"mock_paid_{int(time.time())}"
        }).eq("id", payment["id"]).execute()
        
        supabase.table("jobs").update({"status": "completed"}).eq("id", job_id).execute()
        
        # Credit Worker
        job_info = supabase.table("jobs").select("worker_id").eq("id", job_id).execute().data[0]
        worker_id = job_info["worker_id"]
        w_profile = supabase.table("profiles").select("earnings").eq("id", worker_id).execute().data[0]
        new_earnings = (w_profile.get("earnings") or 0) + payment["worker_earnings"]
        supabase.table("profiles").update({"earnings": new_earnings}).eq("id", worker_id).execute()
        
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/payment/verify", methods=["POST"])
def verify_payment():
    if session.get("role") != "user":
        return jsonify({"success": False, "error": "Unauthorized"}), 403
        
    data = request.json
    try:
        # Verify signature
        razor_client.utility.verify_payment_signature({
            'razorpay_order_id': data.get('razorpay_order_id'),
            'razorpay_payment_id': data.get('razorpay_payment_id'),
            'razorpay_signature': data.get('razorpay_signature')
        })
        
        order_id = data.get('razorpay_order_id')
        
        # Update payment record
        pay_update = {
            "razorpay_payment_id": data.get('razorpay_payment_id'),
            "razorpay_signature": data.get('razorpay_signature'),
            "payment_status": "paid"
        }
        pay_resp = supabase.table("payments").update(pay_update).eq("razorpay_order_id", order_id).execute()
        
        if pay_resp.data:
            job_id = pay_resp.data[0]["job_id"]
            # Update job status
            supabase.table("jobs").update({"status": "completed"}).eq("id", job_id).execute()
            
            # Update worker earnings in profile
            worker_earnings = pay_resp.data[0]["worker_earnings"]
            job_info = supabase.table("jobs").select("worker_id").eq("id", job_id).execute()
            worker_id = job_info.data[0]["worker_id"]
            
            worker_profile = supabase.table("profiles").select("earnings").eq("id", worker_id).execute()
            current_earnings = worker_profile.data[0].get("earnings") or 0
            supabase.table("profiles").update({"earnings": current_earnings + worker_earnings}).eq("id", worker_id).execute()
            
            return jsonify({"success": True})
        
        return jsonify({"success": False, "error": "Payment record not found"}), 404
        
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

@app.route("/api/chat/<int:job_id>", methods=["GET"])
def get_messages(job_id):
    if "user_id" not in session:
        return jsonify({"success": False, "error": "Not logged in"}), 401
    
    # App Lock Check
    if is_user_locked(session.get("user_id")):
        return jsonify({"success": False, "error": "App feature locked. Payment pending."}), 403
    if not supabase:
        return jsonify({"success": False, "error": "Database error"}), 500
        
    try:
        # Fetch messages for this job
        response = supabase.table("messages").select("*").eq("job_id", job_id).order("created_at", desc=False).execute()
        messages = response.data
        return jsonify({"success": True, "messages": messages})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/job/negotiate/accept", methods=["POST"])
def accept_negotiation():
    if not session.get("user_id"):
        return jsonify({"success": False, "error": "Unauthorized"}), 403
        
    data = request.json
    job_id = data.get("job_id")
    
    try:
        job_resp = supabase.table("jobs").select("*").eq("id", job_id).execute()
        job = job_resp.data[0]
        
        # Finalize the price
        new_price = job["bargain_price"]
        update_data = {
            "quoted_price": new_price,
            "status": "quoted", # Move back to quoted so user can accept officially
            "bargain_price": None,
            "bargain_by": None
        }
        supabase.table("jobs").update(update_data).eq("id", job_id).execute()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)
