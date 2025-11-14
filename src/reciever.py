import websockets
import asyncio
from PIL import Image
import io
import logging
from datetime import datetime
import ssl
import numpy as np
import os
import json
import time

# Try to import pyfakewebcam for virtual camera support
try:
    import pyfakewebcam
    FAKE_WEBCAM_AVAILABLE = True
except ImportError:
    FAKE_WEBCAM_AVAILABLE = False
    print("⚠️  pyfakewebcam not installed. Install with: pip install pyfakewebcam")
    print("   Virtual camera will not be available.")

# Suppress WebSocket debug logs
logging.getLogger('websockets').setLevel(logging.ERROR)

# Virtual camera device path (adjust if needed)
VIRTUAL_CAMERA_DEVICE = "/dev/video2"

# Fixed camera dimensions - never change these to avoid reinitialization issues
FIXED_CAMERA_WIDTH = 1920
FIXED_CAMERA_HEIGHT = 1080

# Global camera object - initialized once with fixed dimensions
camera = None

def init_virtual_camera():
    """Initialize virtual camera device with fixed dimensions (only once)"""
    global camera
    
    if not FAKE_WEBCAM_AVAILABLE:
        return False
    
    if not os.path.exists(VIRTUAL_CAMERA_DEVICE):
        print(f"⚠️  Virtual camera device {VIRTUAL_CAMERA_DEVICE} not found!")
        print("   Run: sudo modprobe v4l2loopback video_nr=2 card_label='Mobile Camera' exclusive_caps=1")
        return False
    
    # Initialize only if not already initialized
    if camera is None:
        try:
            camera = pyfakewebcam.FakeWebcam(VIRTUAL_CAMERA_DEVICE, FIXED_CAMERA_WIDTH, FIXED_CAMERA_HEIGHT)
            print(f"✅ Virtual camera initialized: {VIRTUAL_CAMERA_DEVICE} ({FIXED_CAMERA_WIDTH}x{FIXED_CAMERA_HEIGHT})")
            print(f"   Using fixed dimensions to avoid reinitialization issues")
            return True
        except Exception as e:
            print(f"❌ Error initializing camera: {e}")
            return False
    
    return True

def resize_with_letterbox(img, target_width, target_height):
    """Resize image maintaining aspect ratio, adding black bars if needed"""
    img_width, img_height = img.size
    img_aspect = img_width / img_height
    target_aspect = target_width / target_height
    
    if img_aspect > target_aspect:
        # Image is wider - fit to width
        new_width = target_width
        new_height = int(target_width / img_aspect)
    else:
        # Image is taller - fit to height
        new_height = target_height
        new_width = int(target_height * img_aspect)
    
    # Resize maintaining aspect ratio
    resized = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
    
    # Create new image with black background
    result = Image.new('RGB', (target_width, target_height), (0, 0, 0))
    
    # Paste resized image centered
    x_offset = (target_width - new_width) // 2
    y_offset = (target_height - new_height) // 2
    result.paste(resized, (x_offset, y_offset))
    
    return result

def apply_transformations(img, rotation=0, flip_h=False, flip_v=False, target_width=None, target_height=None):
    """Apply rotation and flip transformations to image"""
    # Apply rotation
    if rotation != 0:
        # For 90/270 degree rotations, we need to expand to swap dimensions
        effective_rotation = rotation % 360
        if effective_rotation == 90 or effective_rotation == 270:
            img = img.rotate(-rotation, expand=True)  # Expand to swap dimensions
        else:
            img = img.rotate(-rotation, expand=False)  # Negative for clockwise
    
    # Apply horizontal flip
    if flip_h:
        img = img.transpose(Image.FLIP_LEFT_RIGHT)
    
    # Apply vertical flip
    if flip_v:
        img = img.transpose(Image.FLIP_TOP_BOTTOM)
    
    # Resize to target dimensions using letterboxing (maintains aspect ratio)
    if target_width and target_height:
        if img.size != (target_width, target_height):
            img = resize_with_letterbox(img, target_width, target_height)
    
    return img

def write_frame_to_camera(img, target_width, target_height):
    """Write frame to virtual camera device - always uses fixed dimensions"""
    global camera
    
    if camera is None:
        return False
    
    try:
        # Camera is always fixed dimensions, ensure image matches
        if img.size != (FIXED_CAMERA_WIDTH, FIXED_CAMERA_HEIGHT):
            img = resize_with_letterbox(img, FIXED_CAMERA_WIDTH, FIXED_CAMERA_HEIGHT)
        
        # Convert PIL image to numpy array (RGB format)
        img_array = np.array(img)
        
        # Verify array shape matches expected dimensions
        if img_array.shape[:2] != (FIXED_CAMERA_HEIGHT, FIXED_CAMERA_WIDTH):
            # Resize array if needed
            img_pil = Image.fromarray(img_array)
            img_pil = resize_with_letterbox(img_pil, FIXED_CAMERA_WIDTH, FIXED_CAMERA_HEIGHT)
            img_array = np.array(img_pil)
        
        # Schedule frame to virtual camera
        camera.schedule_frame(img_array)
        return True
    except Exception as e:
        # Don't print every error to avoid spam, but return False
        return False

async def handler(websocket):
    """Handle incoming WebSocket connections"""
    client_ip = websocket.remote_address[0] if websocket.remote_address else "Unknown"
    print(f"✅ Client connected from {client_ip}")
    
    frame_count = 0
    # Track rotation/flip state per connection
    rotation = 0  # 0, 90, 180, 270
    flip_h = False  # Horizontal flip
    flip_v = False  # Vertical flip
    
    # Always use fixed camera dimensions - no reinitialization needed
    output_width = FIXED_CAMERA_WIDTH
    output_height = FIXED_CAMERA_HEIGHT
    
    # Track incoming frame dimensions for stability validation
    last_incoming_dimensions = None
    dimension_change_frame_count = 0
    stable_dimension_frame_count = 0
    REQUIRED_STABLE_FRAMES = 3  # Wait for 3 frames with same dimensions before processing
    
    try:
        async for message in websocket:
            if isinstance(message, bytes):
                try:
                    # Save frame as image
                    img = Image.open(io.BytesIO(message))
                    
                    # Detect orientation from incoming frame dimensions
                    incoming_width, incoming_height = img.size
                    incoming_is_landscape = incoming_width > incoming_height
                    
                    # Validate dimension stability (handle orientation changes gracefully)
                    if last_incoming_dimensions and last_incoming_dimensions != (incoming_width, incoming_height):
                        dimension_change_frame_count += 1
                        stable_dimension_frame_count = 0
                        if dimension_change_frame_count == 1:
                            print(f"🔄 Frame dimension change detected: {last_incoming_dimensions} -> {incoming_width}x{incoming_height}")
                        
                        # Wait for dimensions to stabilize before processing
                        if dimension_change_frame_count < REQUIRED_STABLE_FRAMES:
                            last_incoming_dimensions = (incoming_width, incoming_height)
                            continue  # Skip this frame, wait for stability
                    else:
                        # Dimensions are stable
                        if dimension_change_frame_count > 0:
                            stable_dimension_frame_count += 1
                            # Only process after we've seen stable frames
                            if stable_dimension_frame_count < REQUIRED_STABLE_FRAMES:
                                last_incoming_dimensions = (incoming_width, incoming_height)
                                continue
                            else:
                                # Dimensions have stabilized
                                print(f"✅ Frame dimensions stabilized at {incoming_width}x{incoming_height}")
                                dimension_change_frame_count = 0
                                stable_dimension_frame_count = 0
                    
                    last_incoming_dimensions = (incoming_width, incoming_height)
                    
                    # Always use fixed camera dimensions - no reinitialization needed
                    # Letterboxing will handle aspect ratio differences
                    output_width = FIXED_CAMERA_WIDTH
                    output_height = FIXED_CAMERA_HEIGHT
                    
                    # Apply rotation and flip transformations
                    # Letterboxing will maintain aspect ratio and add black bars if needed
                    img = apply_transformations(img, rotation, flip_h, flip_v, output_width, output_height)
                    
                    # Save to file (optional, for debugging)
                    try:
                        img.save("latest.jpg", quality=100)
                    except Exception as e:
                        print(f"⚠️  Failed to save latest.jpg: {e}")
                    
                    # Write to virtual camera (continue even if it fails)
                    try:
                        write_frame_to_camera(img, output_width, output_height)
                    except Exception as e:
                        # Don't stop processing if camera write fails
                        if frame_count % 30 == 0:  # Only log every 30 frames to avoid spam
                            print(f"⚠️  Camera write error: {e}")
                    
                    frame_count += 1
                    if frame_count % 10 == 0:
                        timestamp = datetime.now().strftime("%H:%M:%S")
                        incoming_orientation = "landscape" if incoming_width > incoming_height else "portrait"
                        print(f"📸 [{timestamp}] Frame #{frame_count} | Input: {incoming_width}x{incoming_height} ({incoming_orientation}) | Output: {output_width}x{output_height} (fixed)")
                        
                except Exception as e:
                    # Log error but continue processing
                    if frame_count % 30 == 0:  # Only log every 30 frames to avoid spam
                        print(f"❌ Image error: {e}")
                    # Continue to next frame
                    continue
            else:
                # Handle text messages (rotation/flip commands)
                try:
                    # Try to parse as JSON first
                    try:
                        cmd = json.loads(message)
                        if cmd.get("action") == "rotate":
                            rotation = int(cmd.get("value", 0))
                            print(f"🔄 Rotation set to {rotation}°")
                        elif cmd.get("action") == "flip":
                            flip_type = cmd.get("type", "").upper()
                            if flip_type == "H":
                                flip_h = cmd.get("value", False)
                                print(f"🔄 Horizontal flip: {flip_h}")
                            elif flip_type == "V":
                                flip_v = cmd.get("value", False)
                                print(f"🔄 Vertical flip: {flip_v}")
                    except json.JSONDecodeError:
                        # Fallback to simple string format
                        if message.startswith("ROTATE:"):
                            rotation = int(message.split(":")[1])
                            print(f"🔄 Rotation set to {rotation}°")
                        elif message.startswith("FLIP:H:"):
                            flip_h = message.split(":")[2].lower() == "true"
                            print(f"🔄 Horizontal flip: {flip_h}")
                        elif message.startswith("FLIP:V:"):
                            flip_v = message.split(":")[2].lower() == "true"
                            print(f"🔄 Vertical flip: {flip_v}")
                except Exception as e:
                    print(f"📝 Message: {message} (parse error: {e})")
                
    except websockets.exceptions.ConnectionClosed:
        print(f"⏹ Client from {client_ip} disconnected after {frame_count} frames")
    except Exception as e:
        print(f"❌ Error from {client_ip}: {type(e).__name__}: {e}")

async def main():
    """Start Secure WebSocket server"""
    print("🚀 Starting Secure WebSocket server...")
    print("   Listen address: 0.0.0.0:8081")
    print("   Protocol: wss:// (secure)")
    print(f"   Using fixed camera dimensions: {FIXED_CAMERA_WIDTH}x{FIXED_CAMERA_HEIGHT}")
    print("   All frames will be letterboxed to maintain aspect ratio")
    
    # Initialize virtual camera once with fixed dimensions
    camera_initialized = init_virtual_camera()
    if not camera_initialized:
        print("⚠️  Continuing without virtual camera (frames will only be saved to latest.jpg)")
    
    # Load SSL certificate
    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_context.load_cert_chain('cert.pem', 'key.pem')
    
    async with websockets.serve(handler, "0.0.0.0", 8081, ssl=ssl_context):
        print("💾 Frames saved to: latest.jpg")
        if camera_initialized:
            print(f"📹 Virtual camera active: {VIRTUAL_CAMERA_DEVICE}")
            print("   Applications can now use 'Mobile Camera' as a webcam input")
        
        try:
            await asyncio.Future()  # run forever
        except KeyboardInterrupt:
            print("\n⏹ Server shutting down...")

if __name__ == "__main__":
    asyncio.run(main())