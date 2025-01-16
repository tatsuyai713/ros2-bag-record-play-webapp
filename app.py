import os
import re
import yaml
import subprocess
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, session, send_from_directory, jsonify
from markupsafe import Markup
from threading import Thread, Lock
import time
import pwd

app = Flask(__name__)
app.secret_key = 'your_secure_secret_key'  

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RECORDINGS_DIR = os.path.join(BASE_DIR, 'recordings')
CONFIGS_DIR = os.path.join(BASE_DIR, 'configs')
os.makedirs(RECORDINGS_DIR, exist_ok=True)
os.makedirs(CONFIGS_DIR, exist_ok=True)

current_recording_proc = None
recording_lock = Lock()

record_output_buffer = []
output_buffer_lock = Lock()

def is_recording():
    with recording_lock:
        if current_recording_proc is None:
            return False
        return (current_recording_proc.poll() is None)

def read_process_output(proc):
    global record_output_buffer

    def _reader(stream):
        while True:
            line = stream.readline()
            if not line:
                break
            with output_buffer_lock:
                record_output_buffer.append(line)

    t_out = Thread(target=_reader, args=(proc.stdout,), daemon=True)
    t_out.start()

    t_err = Thread(target=_reader, args=(proc.stderr,), daemon=True)
    t_err.start()

    proc.wait()

def auto_stop_record_after(duration_seconds):
    interval = 0.1
    elapsed = 0.0

    while elapsed < duration_seconds:
        if not is_recording():
            return
        time.sleep(interval)
        elapsed += interval

    if is_recording():
        stop_ros2_bag_record()

def start_ros2_bag_record(save_folder, topics, duration):
    global current_recording_proc
    global record_output_buffer

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    bag_name = f"recording_{timestamp}"
    full_path = os.path.join(RECORDINGS_DIR, save_folder, bag_name)

    ros_setup_script = '/opt/ros/humble/setup.bash'
    command = f"source {ros_setup_script} && ros2 bag record -o {full_path} "
    for t in topics:
        if t.strip():
            command += f" {t.strip()} "

    command += " -s mcap"

    with recording_lock:
        with output_buffer_lock:
            record_output_buffer.clear()

        current_recording_proc = subprocess.Popen(
            command, shell=True, executable='/bin/bash',
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True
        )

    t = Thread(target=read_process_output, args=(current_recording_proc,), daemon=True)
    t.start()

    duration = duration.strip()
    if duration.isdigit():
        rec_time = int(duration)
        if rec_time > 0:
            auto_thread = Thread(target=auto_stop_record_after, args=(rec_time,), daemon=True)
            auto_thread.start()

def stop_ros2_bag_record():
    global current_recording_proc
    with recording_lock:
        if current_recording_proc is not None:
            if current_recording_proc.poll() is None:
                current_recording_proc.terminate()
            current_recording_proc = None

def is_valid_config_name(name: str) -> bool:
    return bool(re.match(r'^[A-Za-z0-9_]+$', name))

def load_config_from_yaml(config_name: str):
    path = os.path.join(CONFIGS_DIR, f"{config_name}.yaml")
    if not os.path.exists(path):
        return None
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)

def save_config_to_yaml(config_name: str, config_data: dict):
    path = os.path.join(CONFIGS_DIR, f"{config_name}.yaml")
    with open(path, 'w', encoding='utf-8') as f:
        yaml.dump(config_data, f, sort_keys=False, allow_unicode=True)

@app.route('/', methods=['GET'])
def index():
    current_user = pwd.getpwuid(os.getuid()).pw_name
    config = session.get('config', None)

    config_files = []
    if os.path.exists(CONFIGS_DIR):
        for fname in os.listdir(CONFIGS_DIR):
            if fname.endswith('.yaml'):
                config_files.append(fname[:-5]) 
    config_files.sort()

    return render_template('index.html',
                           config=config,
                           config_files=config_files,
                           recording=is_recording(),
                           current_user=current_user)


@app.route('/save_config', methods=['POST'])
def save_config():
    config_name = request.form.get('config_name', '').strip()
    if not config_name:
        flash("Configuration name is required to save.", "error")
        return redirect(url_for('index'))

    if not is_valid_config_name(config_name):
        flash("Invalid configuration name. Only alphanumeric and underscores are allowed.", "error")
        return redirect(url_for('index'))

    save_folder = request.form.get('hidden_save_folder', '').strip()
    duration = request.form.get('hidden_duration', '').strip()
    topics = request.form.getlist('hidden_topics')

    config_data = {
        'save_folder': save_folder,
        'duration': duration,
        'topics': topics
    }

    existing = load_config_from_yaml(config_name)
    if existing:
        flash(f"Configuration \"{config_name}\" was overwritten successfully!", "success")
    else:
        flash(f"Configuration \"{config_name}\" was saved successfully!", "success")

    save_config_to_yaml(config_name, config_data)
    session['config'] = config_data

    return redirect(url_for('index'))


@app.route('/load_config', methods=['POST'])
def load_config():
    config_name = request.form.get('config_name_dropdown', '').strip()
    if not config_name:
        flash("Please select a configuration to load.", "error")
        return redirect(url_for('index'))

    if not is_valid_config_name(config_name):
        flash("Invalid configuration name. Only alphanumeric and underscores are allowed.", "error")
        return redirect(url_for('index'))

    loaded = load_config_from_yaml(config_name)
    if not loaded:
        flash(f"Configuration \"{config_name}\" does not exist.", "error")
        return redirect(url_for('index'))

    session['config'] = loaded
    flash(f"Configuration \"{config_name}\" was loaded successfully!", "success")
    return redirect(url_for('index'))


@app.route('/start_recording', methods=['POST'])
def start_recording_route():
    current_user = pwd.getpwuid(os.getuid()).pw_name  # ???????????
    base_folder = f'/home/{current_user}/'  # ??????????
    save_folder = request.form.get('save_folder', '').strip()

    if not save_folder:
        flash("Save folder is required.", "error")
        return redirect(url_for('index'))

    if not save_folder.startswith(base_folder):
        flash(f"Save folder must be within {base_folder}.", "error")
        return redirect(url_for('index'))
    duration = request.form.get('duration', '').strip()
    topics = request.form.getlist('topics')

    if not save_folder:
        flash("Save folder is required.", "error")
        return redirect(url_for('index'))

    if not topics:
        flash("At least one topic is required.", "error")
        return redirect(url_for('index'))

    config_data = {
        'save_folder': save_folder,
        'duration': duration,
        'topics': topics
    }
    session['config'] = config_data

    start_ros2_bag_record(save_folder, topics, duration)
    flash("Recording started successfully!", "success")

    return redirect(url_for('index'))


@app.route('/stop_recording', methods=['POST'])
def stop_recording_route():
    if is_recording():
        stop_ros2_bag_record()
        flash("Recording stopped.", "success")
    else:
        flash("No recording process is running.", "error")
    return redirect(url_for('index'))


@app.route('/check_recording', methods=['GET'])
def check_recording_route():
    return jsonify({"recording": is_recording()})


@app.route('/configs/<filename>')
def download_config(filename):
    return send_from_directory(CONFIGS_DIR, filename, as_attachment=True)


@app.route('/api/list_topics', methods=['GET'])
def list_ros2_topics():
    try:
        output = subprocess.check_output(['ros2', 'topic', 'list'], text=True)
        topics = [line.strip() for line in output.split('\n') if line.strip()]
        return jsonify({'topics': topics})
    except subprocess.CalledProcessError as e:
        return jsonify({'topics': [], 'error': str(e)}), 500

@app.route('/record_output', methods=['GET'])
def record_output():
    with output_buffer_lock:
        logs = record_output_buffer[:]
    formatted_logs = [log.replace('\n', '<br>') for log in logs]
    return jsonify({"logs": formatted_logs})

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')
