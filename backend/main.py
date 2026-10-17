from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health():
    return {"status": "ok"}


from src.schema import ToolCallRequest
from src.interceptor import intercept
from src.ml_model import available


@app.get("/model-status")
def model_status():
    return {"ml_available": available()}


@app.post("/evaluate")
def evaluate_action(request: ToolCallRequest):
    return intercept(request)
