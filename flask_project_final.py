from flask import Flask, request, render_template_string, jsonify, Response
from gpiozero import Motor, PWMOutputDevice
import cv2
import io
from threading import Condition, Thread
import serial
import time
import json

app = Flask(__name__)


motor1 = Motor(forward=8, backward=7)
motor2 = Motor(forward=20, backward=21)
enable1 = PWMOutputDevice(25)
enable2 = PWMOutputDevice(16)
enable1.value = 0
enable2.value = 0


ser = serial.Serial('/dev/serial0', 9600, timeout=1)

def map_value(x, in_min, in_max, out_min, out_max):
    return (x - in_min) * (out_max - out_min) / (in_max - in_min) + out_min


html_template = """<!DOCTYPE html>
<html>
<head>
    <title>Motor and Gas Sensor Control</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css">
    <script src="http://cdn.rawgit.com/Mikhus/canvas-gauges/gh-pages/download/2.1.7/all/gauge.min.js"></script>
    <style>
        .button { background-color: #4CAF50; border: none; color: white; padding: 12px 28px; font-size: 20px; cursor: pointer; margin: 5px; }
        .button2 { background-color: #555555; }
	#elm{
		display: inline-block
	}
    </style>
    <script>
        function moveForward() { fetch('/forward'); }
        function moveLeft() { fetch('/left'); }
        function stopRobot() { fetch('/stop'); }
        function moveRight() { fetch('/right'); }
        function moveReverse() { fetch('/reverse'); }
        function updateMotorSpeed(pos) {
            document.getElementById('motorSpeed').innerHTML = pos;
            fetch(`/speed?value=${pos}`);
        }

        async function getReadings() {
            const response = await fetch('/readings');
            const data = await response.json();
            gaugeGas.value = data.gas;
        }

        const eventSource = new EventSource('/events');
        eventSource.onmessage = (event) => {
            const data = JSON.parse(event.data);
            gaugeGas.value = data.gas;
        };

        window.onload = getReadings;
    </script>
</head>
<body>
    <div class="container text-center">
	<h1> Streaming </h1>
        <img src="/stream.mjpg" width="320" height="240" />
        <h1>Gauge</h1>
	<canvas id="gauge-gas"></canvas>
        <p>
            <button class="button" onclick="moveForward()">FORWARD</button>
            <button class="button" onclick="moveLeft()">LEFT</button>
            <button class="button button2" onclick="stopRobot()">STOP</button>
            <button class="button" onclick="moveRight()">RIGHT</button>
            <button class="button" onclick="moveReverse()">REVERSE</button>
        </p>
        <p>Motor Speed: <span id="motorSpeed">0</span></p>
        <input type="range" min="0" max="100" step="25" oninput="updateMotorSpeed(this.value)" value="0" />

    </div>
    <script>
        var gaugeGas = new RadialGauge({
            renderTo: 'gauge-gas',
            width: 300,
            height: 300,
            units: "Gas (%)",
            minValue: 0,
            maxValue: 100,
            majorTicks: ["0", "20", "40", "60", "80", "100"],
            minorTicks: 4,
            highlights: [
                { from: 80, to: 100, color: "#ff0000" },
                { from: 60, to: 80, color: "orange" },
                { from: 40, to: 60, color: "lightgreen" }
            ],
            animationDuration: 1500,
        }).draw();
    </script>
</body>
</html>
"""
def stop_motors():
    motor1.stop()
    motor2.stop()

@app.route('/')
def index():
    return render_template_string(html_template)

@app.route('/forward')
def forward():
    stop_motors()
    motor1.forward()
    motor2.forward()
    return '', 200

@app.route('/left')
def left():
    stop_motors()
    motor1.forward()
    motor2.stop()
    return '', 200

@app.route('/stop')
def stop():
    stop_motors()
    return '', 200

@app.route('/right')
def right():
    stop_motors()
    motor1.stop()
    motor2.forward()
    return '', 200

@app.route('/reverse')
def reverse():
    stop_motors()
    motor1.backward()
    motor2.backward()
    return '', 200

@app.route('/speed')
def set_speed():
    value = request.args.get('value', default=0, type=int)
    duty_cycle = value / 100.0
    enable1.value = duty_cycle
    enable2.value = duty_cycle
    return '', 200

@app.route('/readings')
def get_readings():
    if ser.in_waiting > 0:
        data = int(ser.readline().decode('utf-8').strip())
        mapped_data = int(map_value(data, 0, 4095, 0, 100))
        return jsonify({"gas": mapped_data})
    return jsonify({"gas": 0})

@app.route('/events')
def events():
    def generate():
        while True:
            if ser.in_waiting > 0:
                data = int(ser.readline().decode('utf-8').strip())
                mapped_data = int(map_value(data, 0, 4095, 0, 100))
                yield f"data: {json.dumps({'gas': mapped_data})}\n\n"
            time.sleep(0.2)
    return Response(generate(), content_type='text/event-stream')

class StreamingOutput(io.BufferedIOBase):
    def __init__(self):
        self.frame = None
        self.condition = Condition()

    def write(self, frame):
        with self.condition:
            self.frame = frame
            self.condition.notify_all()

output = StreamingOutput()

def capture_frames():
    cap = cv2.VideoCapture(0)
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        frame = cv2.resize(frame, (640, 480))
        _, jpeg_frame = cv2.imencode('.jpg', frame)
        output.write(jpeg_frame.tobytes())
    cap.release()

@app.route('/stream.mjpg')
def video_feed():
    def generate():
        while True:
            with output.condition:
                output.condition.wait()
                frame = output.frame
            yield (b'--FRAME\r\n'
                   b'Content-Type: image/jpeg\r\n'
                   b'Content-Length: ' + f"{len(frame)}".encode() + b'\r\n\r\n' +
                   frame + b'\r\n')
    return Response(generate(), mimetype='multipart/x-mixed-replace; boundary=FRAME')

if __name__ == '__main__':
    try:
        capture_thread = Thread(target=capture_frames)
        capture_thread.daemon = True
        capture_thread.start()
        app.run(host='0.0.0.0', port=5000)
    except KeyboardInterrupt:
        pass
    finally:
        stop_motors()
