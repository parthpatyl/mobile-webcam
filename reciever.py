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
# Default dimensions (portrait)
DEFAULT_WIDTH = 1080
DEFAULT_HEIGHT = 1920

# Global camera object and current dimensions
camera = None
current_width = DEFAULT_WIDTH
current_height = DEFAULT_HEIGHT

def init_virtual_camera(width=None, height=None):
    """Initialize or reinitialize the virtual camera device"""
    global camera, current_width, current_height
    
    if not FAKE_WEBCAM_AVAILABLE:
        return False
    
    if not os.path.exists(VIRTUAL_CAMERA_DEVICE):
        print(f"⚠️  Virtual camera device {VIRTUAL_CAMERA_DEVICE} not found!")
        print("   Run: sudo modprobe v4l2loopback video_nr=2 card_label='Mobile Camera' exclusive_caps=1")
        return False
    
    # Use provided dimensions or current dimensions
    if width is None:
        width = current_width
    if height is None:
        height = current_height
    
    # Check if we need to reinitialize (dimensions changed)
    if camera is not None and (current_width != width or current_height != height):
        print(f"🔄 Reinitializing camera: {current_width}x{current_height} -> {width}x{height}")
        camera = None
    
    # Initialize if needed
    if camera is None:
        try:
            camera = pyfakewebcam.FakeWebcam(VIRTUAL_CAMERA_DEVICE, width, height)
            current_width = width
            current_height = height
            print(f"✅ Virtual camera initialized: {VIRTUAL_CAMERA_DEVICE} ({width}x{height})")
            return True
        except Exception as e:
            print(f"❌ Error initializing camera: {e}")
            return False
    
    return True

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
    
    # Resize to target dimensions if provided (after all transformations)
    if target_width and target_height:
        if img.size != (target_width, target_height):
            img = img.resize((target_width, target_height), Image.Resampling.LANCZOS)
    
    return img

def write_frame_to_camera(img, target_width, target_height):
    """Write frame to virtual camera device"""
    global camera
    
    if camera is None:
        return False
    
    try:
        # Ensure correct size for virtual camera
        if img.size != (target_width, target_height):
            img = img.resize((target_width, target_height), Image.Resampling.LANCZOS)
        
        # Convert PIL image to numpy array (RGB format)
        img_array = np.array(img)
        
        # Schedule frame to virtual camera
        camera.schedule_frame(img_array)
        return True
    except Exception as e:
        print(f"❌ Error writing to camera: {e}")
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
    
    # Track orientation and dimensions
    is_landscape = False
    output_width = DEFAULT_WIDTH
    output_height = DEFAULT_HEIGHT
    
    try:
        async for message in websocket:
            if isinstance(message, bytes):
                try:
                    # Save frame as image
                    img = Image.open(io.BytesIO(message))
                    
                    # Detect orientation from incoming frame dimensions
                    incoming_width, incoming_height = img.size
                    incoming_is_landscape = incoming_width > incoming_height
                    
                    # Determine output dimensions based on orientation and rotation
                    effective_rotation = rotation % 360
                    needs_dimension_swap = (effective_rotation == 90 or effective_rotation == 270)
                    
                    if incoming_is_landscape:
                        # Landscape input: use landscape dimensions
                        base_width = 1920
                        base_height = 1080
                    else:
                        # Portrait input: use portrait dimensions
                        base_width = 1080
                        base_height = 1920
                    
                    # Swap dimensions if rotation requires it
                    if needs_dimension_swap:
                        output_width = base_height
                        output_height = base_width
                    else:
                        output_width = base_width
                        output_height = base_height
                    
                    # Reinitialize camera if dimensions changed
                    if (output_width != current_width or output_height != current_height):
                        init_virtual_camera(output_width, output_height)
                    
                    # Apply rotation and flip transformations
                    # The function will handle resizing to target dimensions after rotation
                    img = apply_transformations(img, rotation, flip_h, flip_v, output_width, output_height)
                    
                    # Save to file (optional, for debugging)
                    img.save("latest.jpg", quality=100)
                    
                    # Write to virtual camera
                    write_frame_to_camera(img, output_width, output_height)
                    
                    frame_count += 1
                    if frame_count % 10 == 0:
                        timestamp = datetime.now().strftime("%H:%M:%S")
                        orientation = "landscape" if output_width > output_height else "portrait"
                        print(f"📸 [{timestamp}] Frame #{frame_count} | {output_width}x{output_height} ({orientation})")
                        
                except Exception as e:
                    print(f"❌ Image error: {e}")
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
    print("   Auto-detecting orientation (portrait/landscape)")
    
    # Initialize virtual camera with default dimensions
    # Will be reinitialized when actual frame dimensions are detected
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