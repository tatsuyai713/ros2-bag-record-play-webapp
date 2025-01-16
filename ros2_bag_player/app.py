import os
import subprocess
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from threading import Thread, Lock

app = Flask(__name__)
app.secret_key = 'your_secure_secret_key'

current_play_proc = None
play_lock = Lock()

play_output_buffer = []
output_buffer_lock = Lock()


def is_playing():
    """現在 ros2 bag play が走っているかどうか"""
    with play_lock:
        if current_play_proc is None:
            return False
        return (current_play_proc.poll() is None)

def read_process_output(proc):
    """ros2 bag play のstdout/stderrを読み取り、play_output_bufferに追記する"""
    def _reader(stream):
        while True:
            line = stream.readline()
            if not line:
                break
            with output_buffer_lock:
                play_output_buffer.append(line)

    t_out = Thread(target=_reader, args=(proc.stdout,), daemon=True)
    t_out.start()

    t_err = Thread(target=_reader, args=(proc.stderr,), daemon=True)
    t_err.start()

    proc.wait()

def start_ros2_bag_play(full_path, rate, start_time, end_time, other_options):
    """ros2 bag playを指定ディレクトリ(またはファイル)で開始"""
    global current_play_proc, play_output_buffer

    ros_setup_script = '/opt/ros/humble/setup.bash'  # 必要に応じて修正
    command = f"source {ros_setup_script} && ros2 bag play '{full_path}'"

    if rate.strip():
        command += f" --rate {rate.strip()}"
    if start_time.strip():
        command += f" --start-offset {start_time.strip()}"
    if end_time.strip():
        command += f" --duration {end_time.strip()}"
    if other_options.strip():
        command += f" {other_options.strip()}"
    command += f" -s mcap"

    with play_lock:
        with output_buffer_lock:
            play_output_buffer.clear()

        current_play_proc = subprocess.Popen(
            command,
            shell=True,
            executable='/bin/bash',
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

    t = Thread(target=read_process_output, args=(current_play_proc,), daemon=True)
    t.start()

def stop_ros2_bag_play():
    """ros2 bag playを停止"""
    global current_play_proc
    with play_lock:
        if current_play_proc is not None:
            if current_play_proc.poll() is None:
                current_play_proc.terminate()
            current_play_proc = None

@app.route('/')
def index():
    """
    メインページ。
    セッションに保存したフォームデータ（base_path / subfolder / optionsなど）を読み出し、
    ページ上のフォームに再表示する。
    """
    form_data = session.get('form_data', {
        'base_path': '',
        'subfolder': '',
        'rate': '',
        'start_time': '',
        'end_time': '',
        'other_options': ''
    })
    return render_template('index.html', form_data=form_data)

@app.route('/api/list_subfolders', methods=['POST'])
def list_subfolders():
    """base_path直下のサブフォルダ一覧を返す (AJAX)"""
    data = request.json or {}
    base_path = data.get('base_path', '').strip()

    if not base_path:
        return jsonify({"subfolders": [], "error": "No base_path provided."}), 400
    if not os.path.exists(base_path):
        return jsonify({"subfolders": [], "error": f"Path does not exist: {base_path}"}), 400
    if not os.path.isdir(base_path):
        return jsonify({"subfolders": [], "error": f"Not a directory: {base_path}"}), 400

    try:
        subfolders = []
        for item in os.listdir(base_path):
            item_path = os.path.join(base_path, item)
            if os.path.isdir(item_path):
                subfolders.append(item)
        subfolders.sort()
        return jsonify({"subfolders": subfolders})
    except Exception as e:
        return jsonify({"subfolders": [], "error": str(e)}), 500

@app.route('/start_playing', methods=['POST'])
def start_playing():
    """
    ユーザーが入力した base_path + subfolder を結合し、ros2 bag playを開始。
    フォームの入力をセッションに保存しておく。
    """
    base_path = request.form.get('base_path', '').strip()
    subfolder = request.form.get('subfolder', '').strip()

    rate = request.form.get('rate', '')
    start_time = request.form.get('start_time', '')
    end_time = request.form.get('end_time', '')
    other_options = request.form.get('other_options', '')

    # フォーム内容をセッションに保存
    session['form_data'] = {
        'base_path': base_path,
        'subfolder': subfolder,
        'rate': rate,
        'start_time': start_time,
        'end_time': end_time,
        'other_options': other_options
    }

    if not base_path:
        flash("Base folder path is required.", "error")
        return redirect(url_for('index'))
    if not subfolder:
        flash("Please select a subfolder.", "error")
        return redirect(url_for('index'))

    full_path = os.path.join(base_path, subfolder)
    if not os.path.exists(full_path):
        flash(f"The path does not exist: {full_path}", "error")
        return redirect(url_for('index'))

    start_ros2_bag_play(full_path, rate, start_time, end_time, other_options)
    flash(f"Started playback for: {full_path}", "success")
    return redirect(url_for('index'))

@app.route('/stop_playing', methods=['POST'])
def stop_playing():
    if is_playing():
        stop_ros2_bag_play()
        flash("Playback stopped.", "success")
    else:
        flash("No active playback.", "error")
    return redirect(url_for('index'))

@app.route('/check_playing', methods=['GET'])
def check_playing():
    """ros2 bag play が実行中かどうかをJSONで返す"""
    return jsonify({"playing": is_playing()})

@app.route('/play_output', methods=['GET'])
def play_output():
    """ros2 bag play の出力を取得して返却"""
    with output_buffer_lock:
        logs = play_output_buffer[:]
    filtered = [line for line in logs if "stdin is not a terminal device" not in line]
    formatted = [line.replace('\n', '<br>') for line in filtered]
    return jsonify({"logs": formatted})

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0')
