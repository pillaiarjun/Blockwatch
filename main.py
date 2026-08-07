from flask import Flask
app = Flask(__name__)

@app.route("/")
def home():
    return "NYC Vision Hack — we're live!"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
