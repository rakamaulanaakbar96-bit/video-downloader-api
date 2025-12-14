from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel
import yt_dlp
import re
import os
import tempfile
import uuid

app = FastAPI(title="Universal Social Media Downloader API")

# CORS middleware for frontend communication
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Temp directory for downloads
DOWNLOAD_DIR = tempfile.gettempdir()


class DownloadRequest(BaseModel):
    url: str


class FormatInfo(BaseModel):
    format_id: str
    ext: str
    resolution: str
    filesize: int | None = None
    filesize_approx: int | None = None
    has_audio: bool = True
    has_video: bool = True


class VideoInfoResponse(BaseModel):
    title: str
    platform: str
    thumbnail: str | None = None
    duration: float | None = None
    formats: list[FormatInfo]


class DownloadByFormatRequest(BaseModel):
    url: str
    format_id: str


def detect_platform(url: str) -> str:
    """Detect the social media platform from the URL."""
    patterns = {
        "youtube": r"(youtube\.com|youtu\.be)",
        "tiktok": r"tiktok\.com",
        "instagram": r"instagram\.com",
        "facebook": r"(facebook\.com|fb\.watch)",
        "twitter": r"(twitter\.com|x\.com)",
    }
    for platform, pattern in patterns.items():
        if re.search(pattern, url, re.IGNORECASE):
            return platform
    return "unknown"


def get_yt_dlp_options(platform: str) -> dict:
    """Get platform-specific yt-dlp options."""
    base_options = {
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
    }

    # TikTok-specific: try to get no-watermark version
    if platform == "tiktok":
        base_options["format_sort"] = ["res", "ext:mp4:m4a"]

    return base_options


def sanitize_filename(filename: str) -> str:
    """Remove invalid characters from filename."""
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        filename = filename.replace(char, "_")
    return filename[:100]  # Limit length


@app.post("/api/info", response_model=VideoInfoResponse)
async def get_video_info(request: DownloadRequest):
    """
    Get video information with all available formats/resolutions.
    """
    url = request.url.strip()

    if not url:
        raise HTTPException(status_code=400, detail="URL is required")

    platform = detect_platform(url)

    if platform == "unknown":
        raise HTTPException(
            status_code=400,
            detail="Unsupported platform. Supported: YouTube, TikTok, Instagram, Facebook, Twitter/X",
        )

    ydl_opts = get_yt_dlp_options(platform)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

            if info is None:
                raise HTTPException(status_code=404, detail="Could not extract video information")

            # Extract available formats
            formats_list = []
            seen_resolutions = set()
            
            raw_formats = info.get("formats", [])
            
            for f in raw_formats:
                ext = f.get("ext", "")
                height = f.get("height")
                width = f.get("width")
                format_id = f.get("format_id", "")
                video_url = f.get("url")
                
                # Skip formats without URL or video
                if not video_url:
                    continue
                
                # Check if has video and audio
                vcodec = f.get("vcodec", "none")
                acodec = f.get("acodec", "none")
                has_video = vcodec != "none" and vcodec is not None
                has_audio = acodec != "none" and acodec is not None
                
                # Skip audio-only for now
                if not has_video:
                    continue
                
                # Build resolution string
                if height:
                    resolution = f"{height}p"
                    if width:
                        resolution = f"{width}x{height}"
                else:
                    resolution = f.get("format_note", "unknown")
                
                # Create unique key to avoid duplicates
                res_key = f"{resolution}_{ext}_{has_audio}"
                if res_key in seen_resolutions:
                    continue
                seen_resolutions.add(res_key)
                
                formats_list.append(FormatInfo(
                    format_id=format_id,
                    ext=ext,
                    resolution=resolution,
                    filesize=f.get("filesize"),
                    filesize_approx=f.get("filesize_approx"),
                    has_audio=has_audio,
                    has_video=has_video,
                ))
            
            # Sort by resolution (height) descending
            formats_list.sort(
                key=lambda x: int(x.resolution.replace("p", "").split("x")[-1]) if x.resolution.replace("p", "").split("x")[-1].isdigit() else 0,
                reverse=True
            )

            return VideoInfoResponse(
                title=info.get("title", "Untitled"),
                platform=platform,
                thumbnail=info.get("thumbnail"),
                duration=info.get("duration"),
                formats=formats_list,
            )

    except yt_dlp.utils.DownloadError as e:
        error_msg = str(e).lower()
        if "private" in error_msg:
            raise HTTPException(status_code=403, detail="This video is private and cannot be accessed")
        elif "unavailable" in error_msg or "not available" in error_msg:
            raise HTTPException(status_code=404, detail="This video is unavailable")
        elif "login" in error_msg or "sign in" in error_msg:
            raise HTTPException(status_code=401, detail="This video requires login to access")
        else:
            raise HTTPException(status_code=400, detail=f"Failed to extract video: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")


@app.post("/api/download")
async def download_video(request: DownloadByFormatRequest):
    """
    Download video with selected format/resolution.
    Downloads via yt-dlp and returns the file.
    """
    url = request.url.strip()
    format_id = request.format_id.strip()

    if not url or not format_id:
        raise HTTPException(status_code=400, detail="URL and format_id are required")

    platform = detect_platform(url)

    if platform == "unknown":
        raise HTTPException(
            status_code=400,
            detail="Unsupported platform",
        )

    # Generate unique filename
    unique_id = str(uuid.uuid4())[:8]
    output_template = os.path.join(DOWNLOAD_DIR, f"{unique_id}_%(title)s.%(ext)s")

    ydl_opts = {
        "format": format_id,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "outtmpl": output_template,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

            if info is None:
                raise HTTPException(status_code=404, detail="Could not extract video information")

            # Get the downloaded file path
            title = sanitize_filename(info.get("title", "video"))
            ext = info.get("ext", "mp4")
            
            # Find the downloaded file
            downloaded_file = None
            for f in os.listdir(DOWNLOAD_DIR):
                if f.startswith(unique_id):
                    downloaded_file = os.path.join(DOWNLOAD_DIR, f)
                    break
            
            if not downloaded_file or not os.path.exists(downloaded_file):
                raise HTTPException(status_code=404, detail="Downloaded file not found")

            # Return file for download
            return FileResponse(
                path=downloaded_file,
                filename=f"{title}.{ext}",
                media_type="video/mp4",
                background=None,  # Don't delete immediately
            )

    except yt_dlp.utils.DownloadError as e:
        raise HTTPException(status_code=400, detail=f"Failed to download: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok"}
