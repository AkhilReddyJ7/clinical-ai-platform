from fastapi import FastAPI

app = FastAPI(
    title="Clinical AI Intelligence Platform",
    version="0.1.0",
    description="Production-grade AI platform for clinical document intelligence",
)


@app.get("/")
async def root():
    return {"message": "Welcome to the Clinical AI Intelligence Platform"}


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "service": "clinical-ai-platform",
        "version": "0.1.0",
    }
