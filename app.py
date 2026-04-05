import os
from io import BytesIO
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import time

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
        return redirect(url_for("user_dashboard" if session.get("role") == "user" else "worker_dashboard"))
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

@app.route("/dashboard/user")
def user_dashboard():
    if session.get("role") != "user":
        return redirect(url_for("home"))
    
    if not supabase:
        return "Database connection failed", 500
        
    workers_response = supabase.table("profiles").select("*").eq("role", "worker").execute()
    workers = workers_response.data
    
    # Fetch real user booking history
    jobs_response = supabase.table("jobs").select("*").eq("user_id", session.get("user_id")).order("created_at", desc=True).execute()
    past_jobs = jobs_response.data
    
    # Map worker names locally to avoid complex join syntax
    worker_lookup = {w["id"]: w["name"] for w in workers}
    
    # Split into active and history
    active_jobs = [j for j in past_jobs if j["status"] in ["pending", "quoted", "accepted", "in_progress"]]
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
        
    return render_template("user_dashboard.html", workers=workers, active_jobs=active_jobs, history_jobs=history_jobs)

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
        
    for j in all_jobs:
        j["user_name"] = user_lookup.get(j["user_id"], "Unknown Customer")
        j["user_phone"] = phone_lookup.get(j["user_id"], "Not Provided")
        
    recent_requests = [j for j in all_jobs if j["status"] in ["pending", "quoted", "accepted", "in_progress"]]
    history_jobs = [j for j in all_jobs if j["status"] in ["completed", "declined"]]
    active_jobs_count = len([j for j in recent_requests if j["status"] == "in_progress"])
    
    return render_template("worker_dashboard.html", worker=worker, total_reviews=total_reviews, recent_requests=recent_requests, history_jobs=history_jobs, active_jobs_count=active_jobs_count)

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
        if role == "worker" and job["worker_id"] != user_id:
            return jsonify({"success": False, "error": "Unauthorized"}), 403
        if role == "user" and job["user_id"] != user_id:
            return jsonify({"success": False, "error": "Unauthorized"}), 403
            
        if role == "user" and new_status not in ["accepted", "declined"]:
             return jsonify({"success": False, "error": "Invalid user action"}), 400
             
        # Worker marking as complete
        if role == "worker" and new_status == "completed" and job["status"] == "in_progress":
            quoted_price = job.get("quoted_price") or 0
            worker_resp = supabase.table("profiles").select("earnings").eq("id", user_id).execute()
            current_earnings = worker_resp.data[0].get("earnings") or 0
            supabase.table("profiles").update({"earnings": current_earnings + quoted_price}).eq("id", user_id).execute()
            
        supabase.table("jobs").update({"status": new_status}).eq("id", job_id).execute()
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
    return render_template("profile.html", profile=profile, reviews=reviews_data, user_jobs=user_jobs, is_owner=is_owner)

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

@app.route("/api/chat/<int:job_id>", methods=["GET"])
def get_messages(job_id):
    if "user_id" not in session:
        return jsonify({"success": False, "error": "Unauthorized"}), 403
    if not supabase:
        return jsonify({"success": False, "error": "Database error"}), 500
        
    try:
        # Fetch messages for this job
        response = supabase.table("messages").select("*").eq("job_id", job_id).order("created_at", desc=False).execute()
        messages = response.data
        return jsonify({"success": True, "messages": messages})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=True, port=5000)
