import os
import json
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from database import db, create_document, get_documents

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class StrokeModel(BaseModel):
    points: List[List[float]] = Field(..., description="[[x,y], ...] in canvas coords")
    color: str = Field("#ff0055")
    size: float = Field(8, ge=1, le=100)
    user: Optional[str] = None


def serialize_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
    d = dict(doc)
    if d.get("_id") is not None:
        d["id"] = str(d.pop("_id"))
    # Convert datetime to isoformat if present
    for k in ("created_at", "updated_at"):
        if k in d and hasattr(d[k], "isoformat"):
            d[k] = d[k].isoformat()
    return d


@app.get("/")
def read_root():
    return {"message": "Graffiti Wall API"}


@app.get("/api/strokes")
def list_strokes(limit: int = 1000):
    if db is None:
        return {"strokes": []}
    docs = get_documents("stroke", {}, limit)
    return {"strokes": [serialize_doc(d) for d in docs]}


@app.post("/api/strokes")
def create_stroke(stroke: StrokeModel):
    stroke_id = create_document("stroke", stroke.model_dump())
    return {"id": stroke_id}


@app.get("/test")
def test_database():
    """Test endpoint to check if database is available and accessible"""
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }

    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Configured"
            response["database_name"] = db.name if hasattr(db, 'name') else "✅ Connected"
            response["connection_status"] = "Connected"
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️  Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"

    # Check environment variables
    response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set"

    return response


# Simple in-process connection manager for WebSocket broadcasting
class ConnectionManager:
    def __init__(self):
        self.active: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active:
            self.active.remove(websocket)

    async def broadcast(self, message: str, sender: Optional[WebSocket] = None):
        dead: List[WebSocket] = []
        for connection in self.active:
            if sender is not None and connection is sender:
                continue
            try:
                await connection.send_text(message)
            except Exception:
                dead.append(connection)
        for d in dead:
            self.disconnect(d)


manager = ConnectionManager()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        # On connect, send recent strokes so new user sees the wall
        try:
            initial = list_strokes().get("strokes", [])
        except Exception:
            initial = []
        await websocket.send_text(json.dumps({"type": "init", "strokes": initial}))

        while True:
            data = await websocket.receive_text()
            try:
                payload = json.loads(data)
            except Exception:
                continue
            if payload.get("type") == "stroke":
                try:
                    stroke = StrokeModel(**payload.get("stroke", {}))
                except Exception:
                    continue
                # Persist
                try:
                    sid = create_document("stroke", stroke.model_dump())
                except Exception:
                    sid = None
                # Broadcast to others
                message = {
                    "type": "stroke",
                    "stroke": {**stroke.model_dump(), "id": sid},
                }
                await manager.broadcast(json.dumps(message), sender=websocket)
    except WebSocketDisconnect:
        manager.disconnect(websocket)


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
