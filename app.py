

# --- Responsive YouTube Comments Fetcher UI and API ---
import random
import difflib
import time
import json
import re
import os
from flask import Flask, render_template, request, redirect, url_for, jsonify
from dotenv import load_dotenv
from googleapiclient.discovery import build

load_dotenv()
API_KEY = os.getenv("YOUTUBE_API_KEY")
app = Flask(__name__)


@app.route('/youtube-comments-ui')
def youtube_comments_ui():
    return render_template('youtube_comments.html')


@app.route('/api/youtube_comments', methods=['POST'])
def api_youtube_comments():
    data = request.get_json()
    video_url = data.get('video_url', '')
    min_subs = data.get('min_subs', '')
    try:
        min_subs = int(min_subs) if str(min_subs).strip().isdigit() else 0
        video_id = extract_video_id(video_url)
        comments = get_comments(video_id)
        youtube = build('youtube', 'v3', developerKey=API_KEY)
        sub_cache = load_sub_cache()
        result = []
        for c in comments:
            subs = 0
            if c.get("author_channel_id"):
                subs, _ = get_subscriber_count_and_name(
                    youtube, c["author_channel_id"], sub_cache)
            if subs >= min_subs:
                result.append({"subs": subs, "text": c["text"]})
        return jsonify({"comments": result})
    except Exception as e:
        return jsonify({"error": str(e)})


# All routes must be below this line


@app.route('/about')
def about():
    return render_template('about.html')
    
@app.route('/sitemap.xml')
def sitemap():
    return render_template('sitemap.xml')

@app.route('/home', methods=['GET', 'POST'])
def home():
    if request.method == 'POST':
        feature = request.form.get('feature')
        if feature == 'filter':
            return redirect(url_for('index'))
        elif feature == 'picker':
            return redirect(url_for('random_comment_picker'))
    return render_template('home.html')


def get_video_owner_channel_id(video_id):
    youtube = build('youtube', 'v3', developerKey=API_KEY)
    response = youtube.videos().list(
        part="snippet",
        id=video_id
    ).execute()
    items = response.get("items", [])
    if not items:
        return None
    return items[0]["snippet"]["channelId"]


def is_subscribed_to_channel(youtube, user_channel_id, owner_channel_id):
    # The YouTube Data API does not allow checking if a user is subscribed to a channel unless the user authenticates.
    # For demo, always return True. In real use, this would require OAuth and user consent.
    return True


@app.route('/random-comment-picker', methods=['GET', 'POST'])
def random_comment_picker():
    winners = []
    error = None
    video_url = ''
    num_winners = 1
    if request.method == 'POST':
        video_url = request.form.get('video_url', '')
        num_winners = int(request.form.get('num_winners', 1))
        video_id = extract_video_id(video_url)
        try:
            owner_channel_id = get_video_owner_channel_id(video_id)
            if not owner_channel_id:
                raise Exception('Could not find video owner channel.')
            fetched_comments = get_comments(video_id)
            youtube = build('youtube', 'v3', developerKey=API_KEY)
            eligible_comments = []
            for c in fetched_comments:
                user_channel_id = c.get("author_channel_id")
                if user_channel_id and is_subscribed_to_channel(youtube, user_channel_id, owner_channel_id):
                    eligible_comments.append(c)
            if not eligible_comments:
                raise Exception(
                    'No eligible comments found (all commenters must be subscribed to the video owner).')
            # Prepare sub cache for efficiency
            sub_cache = load_sub_cache()
            # Pick winners
            picked = random.sample(eligible_comments, min(
                num_winners, len(eligible_comments)))
            winners = []
            for c in picked:
                channel_id = c.get("author_channel_id")
                subs = 0
                channel_name = "Unknown"
                channel_image = None
                if channel_id:
                    subs, channel_name = get_subscriber_count_and_name(
                        youtube, channel_id, sub_cache)
                    # Try to get channel image
                    try:
                        resp = youtube.channels().list(part="snippet", id=channel_id).execute()
                        channel_image = resp["items"][0]["snippet"].get(
                            "thumbnails", {}).get("default", {}).get("url")
                    except Exception:
                        channel_image = None
                winners.append({
                    "text": c.get("text", ""),
                    "channel_id": channel_id,
                    "channel_name": channel_name,
                    "channel_image": channel_image,
                    "subs": subs,
                    "like_count": c.get("like_count", 0)
                })
        except Exception as e:
            error = str(e)
    return render_template('random_picker.html', winners=winners, error=error, video_url=video_url, num_winners=num_winners)


def extract_video_id(url):
    regex = r"(?:v=|\/)([0-9A-Za-z_-]{11}).*"
    match = re.search(regex, url)
    if match:
        return match.group(1)
    else:
        return url  # fallback if user enters just the ID


def load_sub_cache(cache_file="sub_cache.json"):
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_sub_cache(cache, cache_file="sub_cache.json"):
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(cache, f)
    except Exception:
        pass


def get_subscriber_count_and_name(youtube, channel_id, cache, cache_file="sub_cache.json", update_days=3):
    now = int(time.time())
    entry = cache.get(channel_id)
    if entry:
        subs, last_update, channel_name = entry
        if now - last_update < update_days * 86400:
            return subs, channel_name
    try:
        response = youtube.channels().list(
            part="statistics,snippet",
            id=channel_id
        ).execute()
        stats = response["items"][0]["statistics"]
        snippet = response["items"][0]["snippet"]
        subs = int(stats.get("subscriberCount", 0))
        channel_name = snippet.get("title", "Unknown")
        cache[channel_id] = [subs, now, channel_name]
        save_sub_cache(cache, cache_file)
        return subs, channel_name
    except Exception:
        if entry:
            return entry[0], entry[2]
        return 0, "Unknown"


def get_comments(video_id):
    youtube = build('youtube', 'v3', developerKey=API_KEY)
    comments = []
    request = youtube.commentThreads().list(
        part="snippet",
        videoId=video_id,
        maxResults=100,
        textFormat="plainText"
    )
    response = request.execute()
    for item in response.get("items", []):
        comment_snippet = item["snippet"]["topLevelComment"]["snippet"]
        author_channel_id = comment_snippet.get(
            "authorChannelId", {}).get("value")
        comment = {
            "text": comment_snippet["textDisplay"],
            "author_channel_id": author_channel_id,
            "like_count": comment_snippet.get("likeCount", 0),
            "published_at": comment_snippet.get("publishedAt", "")
        }
        comments.append(comment)
    return comments


@app.route('/', methods=['GET', 'POST'])
def index():
    comments = []
    error = None
    suggested_comments = []
    top_subs_comments = []
    all_comments_by_date = []
    all_comments_by_likes = []
    if request.method == 'POST':
        url = request.form.get('video_url', '')
        video_id = extract_video_id(url)
        try:
            fetched_comments = get_comments(video_id)
            youtube = build('youtube', 'v3', developerKey=API_KEY)
            sub_cache = load_sub_cache()
            for c in fetched_comments:
                subs = 0
                channel_name = "Unknown"
                if c["author_channel_id"]:
                    subs, channel_name = get_subscriber_count_and_name(
                        youtube, c["author_channel_id"], sub_cache)
                comments.append({
                    "subs": subs,
                    "channel_name": channel_name,
                    "text": c["text"],
                    "like_count": c["like_count"],
                    "published_at": c["published_at"]
                })
            # Only show question-type comments in suggested section

            def is_question(text):
                # Common question words in English, Spanish, French, German, Hindi, Bengali, etc.
                question_words = [
                    'what', 'why', 'how', 'when', 'where', 'who', 'which', 'whose', 'whom', 'is', 'are', 'can', 'could', 'would', 'should', 'do', 'does', 'did', 'will', 'am', 'may', 'might', 'have', 'has', 'had',
                    '¿', 'por qué', 'qué', 'cuándo', 'dónde', 'quién', 'cuál', 'cómo',  # Spanish
                    'comment', 'pourquoi', 'quand', 'où', 'qui', 'quel', 'quelle', 'lequel',  # French
                    'was', 'warum', 'wie', 'wann', 'wo', 'wer', 'welche',  # German
                    'क्या', 'क्यों', 'कैसे', 'कब', 'कहाँ', 'कौन',  # Hindi
                    'কি', 'কেন', 'কিভাবে', 'কখন', 'কোথায়', 'কারা',  # Bengali
                ]
                text_l = text.strip().lower()
                if '?' in text:
                    return True
                for w in question_words:
                    if text_l.startswith(w + ' '):
                        return True
                return False

            def is_similar(a, b, threshold=0.85):
                return difflib.SequenceMatcher(None, a, b).ratio() > threshold

            # Filter for question-type comments
            question_comments = [c for c in comments if is_question(c["text"])]
            # Deduplicate similar question comments, sort by subs and likes
            deduped = []
            for c in sorted(question_comments, key=lambda x: (x["subs"], x["like_count"]), reverse=True):
                if not any(is_similar(c["text"], d["text"]) for d in deduped):
                    deduped.append(c)
            suggested_comments = deduped[:5]
            # All comments by subscriber count (max to min)
            top_subs_comments = sorted(
                comments, key=lambda x: x["subs"], reverse=True)
            # All comments by date (newest first)
            all_comments_by_date = sorted(
                comments, key=lambda x: x["published_at"], reverse=True)
            # All comments by like count (max to min), then by date (newest first)
            all_comments_by_likes = sorted(
                comments, key=lambda x: (x["like_count"], x["published_at"]), reverse=True)
        except Exception as e:
            error = str(e)
    return render_template('index.html',
                           comments=comments,
                           error=error,
                           suggested_comments=suggested_comments,
                           top_subs_comments=top_subs_comments,
                           all_comments_by_date=all_comments_by_date,
                           all_comments_by_likes=all_comments_by_likes)


if __name__ == "__main__":
    # রেন্ডার থেকে 'PORT' এনভায়রনমেন্ট ভেরিয়েবল নাও, না পেলে ডিফল্ট হিসেবে 5000 ব্যবহার করো
    port = int(os.environ.get("PORT", 5000))
    # হোস্ট 0.0.0.0 সেট করো, যাতে এটি বাইরের কানেকশন গ্রহণ করতে পারে
    app.run(host='0.0.0.0', port=port, debug=True)

