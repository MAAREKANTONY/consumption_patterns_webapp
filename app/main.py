import os, json, uuid, pathlib
from fastapi import FastAPI, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .db import engine, SessionLocal, Base
from .models import Pattern, Upload, RunResult
from .services.signature import compute_signature
from .services.distance import euclidean_distance, softmax_prob_from_dist

app = FastAPI(title="Consumption Patterns WebApp")
templates = Jinja2Templates(directory="app/templates")
Base.metadata.create_all(bind=engine)

UPLOAD_DIR = os.getenv("UPLOAD_DIR", "/data/uploads")

def _db():
    return SessionLocal()

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

# -------- Patterns --------
@app.get("/patterns", response_class=HTMLResponse)
def patterns_list(request: Request):
    db: Session = _db()
    patterns = db.query(Pattern).order_by(Pattern.country_profile, Pattern.pattern_id).all()
    db.close()
    return templates.TemplateResponse("patterns.html", {"request": request, "patterns": patterns})

@app.post("/patterns")
def patterns_create(
    pattern_id: str = Form(...),
    label: str = Form(...),
    country_profile: str = Form(...),
    json_definition: str = Form(...),
):
    # store 1 pattern per row (exactly what you want)
    db: Session = _db()
    p = Pattern(
        pattern_id=pattern_id.strip(),
        label=label.strip(),
        country_profile=country_profile.strip().upper(),
        json_definition=json_definition.strip(),
    )
    db.add(p)
    db.commit()
    db.close()
    return RedirectResponse(url="/patterns", status_code=303)

# -------- Import + Run --------
@app.get("/import", response_class=HTMLResponse)
def import_form(request: Request):
    return templates.TemplateResponse("import.html", {"request": request})

@app.post("/import")
async def import_csv(country_profile: str = Form(...), file: UploadFile = File(...)):
    country_profile = (country_profile or "FR").upper()
    os.makedirs(UPLOAD_DIR, exist_ok=True)

    safe_name = f"{uuid.uuid4().hex}_{pathlib.Path(file.filename).name}"
    stored_path = str(pathlib.Path(UPLOAD_DIR) / safe_name)

    with open(stored_path, "wb") as f:
        f.write(await file.read())

    db: Session = _db()
    up = Upload(country_profile=country_profile, filename=file.filename, stored_path=stored_path)
    db.add(up)
    db.commit()
    db.refresh(up)

    # compute signature + score
    sig = compute_signature(stored_path, country_profile)

    patterns = db.query(Pattern).filter(Pattern.country_profile == country_profile).all()
    scored = []
    if sig["stats"]["revenue_total_classified"] > 0 and patterns:
        # signature dimensions are already in the same shape as patterns dimensions
        sig_dims = {
            "revenue_by_momentum": sig["revenue_by_momentum"],
            "category_mix_by_momentum": sig["category_mix_by_momentum"],
        }
        dists = []
        for p in patterns:
            try:
                pat = json.loads(p.json_definition)
                pat_dims = pat.get("dimensions", {})
                d = euclidean_distance(sig_dims, pat_dims)
            except Exception:
                d = float("inf")
            dists.append(d)
            scored.append({"pattern_id": p.pattern_id, "label": p.label, "distance": d})

        probs = softmax_prob_from_dist(dists, temperature=1.0)
        for i, pr in enumerate(probs):
            scored[i]["probability"] = pr
        scored.sort(key=lambda x: x["distance"])

        selected = scored[0]["pattern_id"] if scored and scored[0]["distance"] != float("inf") else None
    else:
        selected = None

    rr = RunResult(
        upload_id=up.id,
        country_profile=country_profile,
        selected_pattern_id=selected,
        signature_json=json.dumps(sig, ensure_ascii=False),
        scores_json=json.dumps(scored, ensure_ascii=False),
    )
    db.add(rr)
    db.commit()
    db.refresh(rr)
    db.close()

    return RedirectResponse(url=f"/runs/{rr.id}", status_code=303)

@app.get("/runs/{run_id}", response_class=HTMLResponse)
def run_result(request: Request, run_id: int):
    db: Session = _db()
    rr = db.query(RunResult).filter(RunResult.id == run_id).first()
    if not rr:
        db.close()
        return HTMLResponse("Run not found", status_code=404)

    sig = json.loads(rr.signature_json) if rr.signature_json else {}
    scores = json.loads(rr.scores_json) if rr.scores_json else []
    db.close()
    return templates.TemplateResponse(
        "run_result.html",
        {
            "request": request,
            "run": rr,
            "signature": sig,
            "scores": scores,
        },
    )
