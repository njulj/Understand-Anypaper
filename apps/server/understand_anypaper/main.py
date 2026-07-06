from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from understand_anypaper.api.routes import router
import logging

logging.basicConfig(level=logging.INFO)

logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("uvicorn.error").setLevel(logging.WARNING)

app = FastAPI(title="Understand Anypaper API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
