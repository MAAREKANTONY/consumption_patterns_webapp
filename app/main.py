from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
import json

from .db import engine, SessionLocal, Base
from .models import Pattern

app = FastAPI()
templates = Jinja2Templates(directory="app/templates")

Base.metadata.create_all(bind=engine)

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/patterns", response_class=HTMLResponse)
def list_patterns(request: Request):
    db: Session = SessionLocal()
    patterns = db.query(Pattern).all()
    db.close()
    return templates.TemplateResponse("patterns.html", {"request": request, "patterns": patterns})

@app.post("/patterns")
def create_pattern(name: str = Form(...), country: str = Form(...), json_definition: str = Form(...)):
    db: Session = SessionLocal()
    pattern = Pattern(name=name, country=country, json_definition=json_definition)
    db.add(pattern)
    db.commit()
    db.close()
    return {"status": "saved"}
