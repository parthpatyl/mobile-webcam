#!/bin/bash
cd /media/prth/GameSpace/Sidequest
source venv/bin/activate

# Start both servers in background
python3 src/https_server.py &
HTTPS_PID=$!
python3 src/reciever.py &
RECEIVER_PID=$!

echo "✅ Mobile-WebCam servers started!"
echo "   HTTPS Server: PID $HTTPS_PID"
echo "   WebSocket Receiver: PID $RECEIVER_PID"
echo ""
echo "Press Ctrl+C to stop both servers"

# Wait for interrupt
trap "kill $HTTPS_PID $RECEIVER_PID 2>/dev/null; exit" INT
wait

