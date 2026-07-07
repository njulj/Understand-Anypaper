from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from understand_anypaper.api.routes import router
from understand_anypaper.observability import configure_observability
import logging
import uvicorn

logging.basicConfig(level=logging.WARNING)

logging.getLogger("understand_anypaper").setLevel(logging.INFO)

configure_observability()

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

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
