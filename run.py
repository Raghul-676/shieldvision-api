from app import create_app

app = create_app()

if __name__ == '__main__':
    # use_reloader=False is critical for MJPEG streaming:
    # Werkzeug's reloader forks a child process which breaks
    # the threading model and causes stream freezes.
    app.run(debug=True, port=5001, threaded=True, use_reloader=False)
