# app.py
import streamlit as st
from supabase import create_client, Client
from streamlit_audio_recorder import audio_recorder
from bs4 import BeautifulSoup
import requests
import os
from dotenv import load_dotenv
import nltk
from nltk.corpus import wordnet
import random
import language_tool_python
from urllib.parse import urljoin
import datetime
import stripe

# First-time downloads
nltk.download('punkt')
nltk.download('wordnet')
nltk.download('omw-1.4')

load_dotenv()

# ---------- CONFIG ----------
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE")  # For server-only operations; for client use anon key
SUPABASE_ANON = os.getenv("SUPABASE_ANON")  # use anon for client-side
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")  # optional, for payment flow
STRIPE_PUBLISHABLE = os.getenv("STRIPE_PUBLISHABLE_KEY")

if not SUPABASE_URL or not (SUPABASE_KEY or SUPABASE_ANON):
    st.error("Supabase keys not found. Add SUPABASE_URL and SUPABASE_ANON (or SERVICE_ROLE) in .env.")
    st.stop()

# Create Supabase clients (server and client)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_ANON or SUPABASE_KEY)

# grammar tool
tool = language_tool_python.LanguageTool('en-US')

# ---------- Helper utilities ----------
def paraphrase_simple(text):
    """
    Lightweight rephrasing function WITHOUT GenAI:
    - splits into sentences
    - applies synonym substitution on some nouns/adjectives using WordNet
    - shuffles some clauses.
    NOTE: this is intentionally conservative to avoid factual distortion.
    """
    from nltk.tokenize import sent_tokenize, word_tokenize, pos_tag
    sents = sent_tokenize(text)
    out_sents = []
    for sent in sents:
        words = word_tokenize(sent)
        tags = nltk.pos_tag(words)
        new_words = []
        for w, t in tags:
            # attempt synonyms for adjectives and nouns sometimes
            if t.startswith('NN') or t.startswith('JJ'):
                if random.random() < 0.28:
                    syns = wordnet.synsets(w)
                    if syns:
                        lemmas = [l.name().replace('_', ' ') for s in syns for l in s.lemmas()]
                        lemmas = [x for x in lemmas if x.lower() != w.lower()]
                        if lemmas:
                            w = random.choice(lemmas)
            new_words.append(w)
        out_sents.append(' '.join(new_words))
    # maybe shuffle sentences a bit (to create novel ordering)
    if len(out_sents) > 1 and random.random() < 0.3:
        random.shuffle(out_sents)
    return ' '.join(out_sents)

def generate_question_from_text(text, topic):
    """
    Create an advanced, targeted question from the article text.
    Strategy:
      - pick a paragraph, identify important nouns, make scenario-based or 'how would you' questions.
    """
    paras = [p for p in text.split('\n') if p.strip()]
    if not paras: paras = [text]
    para = random.choice(paras[:5])  # prefer first few paragraphs
    # pick nouns via simple heuristic
    tokens = nltk.word_tokenize(para)
    tags = nltk.pos_tag(tokens)
    nouns = [w for w, t in tags if t.startswith('NN') and len(w) > 3]
    nouns = list(dict.fromkeys(nouns))
    chosen = nouns[:3] if nouns else []
    # craft tough question
    if chosen:
        core = ', '.join(chosen[:2])
        q = f"In a production scenario involving {core}, what are the top non-obvious trade-offs you would evaluate, and how would you mitigate the top two risks? (Tie it into {topic} context.)"
    else:
        q = f"Describe an advanced challenge in {topic} that can arise from the technology discussed in the source, and propose a step-by-step resolution strategy."
    return q

# Simple function to fetch & extract textual content from a given URL (first-level)
def fetch_text_from_url(url, max_chars=2000):
    try:
        resp = requests.get(url, timeout=8, headers={'User-Agent':'ARTyBot/1.0'})
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
        # try article tags, then paragraphs
        article = soup.find('article')
        if article:
            text = ' '.join([p.get_text(separator=' ', strip=True) for p in article.find_all('p')])
        else:
            paras = soup.find_all('p')
            text = ' '.join([p.get_text(separator=' ', strip=True) for p in paras])
        if len(text) > max_chars:
            text = text[:max_chars]
        return text
    except Exception as e:
        return None

# ---------- UI ----------
st.set_page_config(page_title="AI Interview Coach by ARTy (ARTy)", layout="wide")
st.title("AI Interview Coach by ARTy 🎤🤖")
st.caption("Mock interviews (tech & managerial). Generate Qs → record answers → get feedback. Uses Supabase for auth & storage.")

# ---- sidebar: auth & basic controls ----
st.sidebar.header("Account / Session")
auth_action = st.sidebar.selectbox("Action", ["Sign In / Create Account", "Sign Out", "Use Guest Mode"])
if auth_action == "Sign Out":
    try:
        supabase.auth.sign_out()
        st.sidebar.success("Signed out.")
    except Exception:
        st.sidebar.warning("Sign out attempted.")
if auth_action == "Sign In / Create Account":
    # Minimal client-side sign-in UI using Supabase
    email = st.sidebar.text_input("Email")
    password = st.sidebar.text_input("Password", type="password")
    if st.sidebar.button("Sign In"):
        # sign in
        try:
            user = supabase.auth.sign_in(email=email, password=password)
            st.sidebar.success("Sign in attempted. Check your inbox or app state.")
        except Exception as e:
            st.sidebar.error("Sign-in failed: " + str(e))
    if st.sidebar.button("Sign Up"):
        try:
            supabase.auth.sign_up({"email": email, "password": password})
            st.sidebar.info("Sign-up attempted. Check your email for confirmation (if enabled).")
        except Exception as e:
            st.sidebar.error("Sign-up failed: " + str(e))

st.sidebar.markdown("---")
st.sidebar.markdown("Demo notes: For production, set up Supabase Auth + policies and use anon/public and secure server roles appropriately.")

# load user id if available (supabase stores session)
session = None
try:
    session = supabase.auth.get_session()
    current_user = session.user.id if session and session.user else None
except Exception:
    current_user = None

if not current_user:
    current_user = "guest_" + (st.session_state.get("guest_id") or str(random.randint(1000,9999)))
    st.session_state["guest_id"] = current_user

st.sidebar.write("User:", current_user)

# ---------- Tabs ----------
tabs = st.tabs(["Generate", "Record", "Practice (writing)", "Read / Topic", "Review Past", "Payment / Upgrade"])

# ---------- GENERATE TAB ----------
with tabs[0]:
    st.header("1) Generate Questions")
    st.write("Choose topic(s), number of questions (30–75). We will fetch online content to build *unique, tough* questions (rephrased).")
    col1, col2 = st.columns([2,1])
    with col1:
        topic = st.text_input("Primary topic (e.g., DevOps, Cloud, RPA, Kubernetes, People Management)", value="DevOps")
        extra_urls = st.text_area("Optional: paste 1+ source URLs (one per line) to seed the questions", placeholder="https://example.com/article1\nhttps://example.com/article2")
    with col2:
        n_q = st.slider("Number of questions", min_value=30, max_value=75, value=40)
        generate_btn = st.button("Generate Questions")
    if generate_btn:
        # create a new interview row
        res = supabase.table("interviews").insert({"user_id": current_user}).execute()
        if res.status_code not in (200,201,204):
            st.error("Failed to create interview in DB. Check Supabase config.")
        interview_id = res.data[0]['id'] if res.data else None
        st.session_state['last_interview_id'] = interview_id
        st.success(f"Interview created: {interview_id}. Now generating {n_q} questions...")

        # Approach:
        #  - If user supplied URLs: fetch text from them.
        #  - Otherwise: attempt a quick heuristic: use official pages in a default set (local fallback).
        urls = [u.strip() for u in (extra_urls.splitlines() if extra_urls else []) if u.strip()]
        seed_texts = []
        for u in urls:
            t = fetch_text_from_url(u)
            if t:
                seed_texts.append((u, t))
        if not seed_texts:
            # fallback: look for some canonical sources for the topic (simplified)
            st.info("No valid URLs provided or fetch failed — using default curated sources (lightweight).")
            default_lookup = {
                "devops": ["https://aws.amazon.com/devops/what-is-devops/"],
                "cloud": ["https://azure.microsoft.com/en-us/overview/what-is-cloud-computing/"],
                "rpa": ["https://www.uipath.com/rpa/robotic-process-automation"]
            }
            key = topic.lower().split()[0]
            for u in default_lookup.get(key, []):
                t = fetch_text_from_url(u)
                if t:
                    seed_texts.append((u,t))
        # if still empty, create generic prompts from topic
        if not seed_texts:
            seed_texts = [("local_fallback", f"This is a fallback paragraph about {topic}. Focus on real-world constraints, scaling, security, and maintainability.")]

        saved_qs = []
        for i in range(n_q):
            src_url, text = random.choice(seed_texts)
            q_text = generate_question_from_text(text, topic)
            # rephrase the question to avoid exact wording using paraphrase_simple
            q_text = paraphrase_simple(q_text)
            saved_qs.append({"interview_id": interview_id, "topic": topic, "q_text": q_text, "source_url": src_url})
            # batch insert every 15 questions
            if len(saved_qs) >= 15:
                supabase.table("questions").insert(saved_qs).execute()
                saved_qs = []
        if saved_qs:
            supabase.table("questions").insert(saved_qs).execute()
        st.success(f"Generated and saved {n_q} questions. You can now go to the Record tab to answer them (tab locked until generation).")
        # mark that generation done
        st.session_state['generated'] = True

    # Show a preview of questions if present
    if st.session_state.get('last_interview_id'):
        preview = supabase.table("questions").select("*").eq("interview_id", st.session_state['last_interview_id']).limit(10).execute()
        st.subheader("Preview (first 10 questions)")
        if preview.data:
            for idx, q in enumerate(preview.data, start=1):
                st.markdown(f"**Q{idx}.** {q['q_text']}")
                if q.get('source_url') and q['source_url'] != 'local_fallback':
                    st.caption(q['source_url'])
        else:
            st.write("No questions yet. Click Generate.")

# ---------- RECORD TAB ----------
with tabs[1]:
    st.header("2) Record Answers (audio + save)")
    if not st.session_state.get('generated'):
        st.warning("Recording is disabled until you generate questions in the Generate tab for this session.")
    else:
        interview_id = st.session_state.get('last_interview_id')
        # fetch questions
        qres = supabase.table("questions").select("*").eq("interview_id", interview_id).execute()
        questions = qres.data or []
        q_index = st.number_input("Question index", min_value=1, max_value=max(1, len(questions)), value=1)
        q = questions[q_index-1] if questions else {"q_text":"No questions found."}
        st.subheader(f"Q{q_index}: {q['q_text']}")
        if q.get('source_url') and q['source_url'] != 'local_fallback':
            st.caption(q['source_url'])
        st.write("Record your answer (audio) — or paste text below.")
        # audio recorder
        audio_bytes = audio_recorder(text="Press to record your answer (webcam/mic permission required).")
        answer_text = st.text_area("Or paste your written answer (optional).")

        if audio_bytes:
            # upload audio to Supabase Storage: placeholder - supabase.from_... (requires bucket configured)
            # We'll store audio as binary in Storage or provide a signed URL. For demo we'll save a local file and upload
            fn = f"answer_{current_user}_{datetime.datetime.utcnow().isoformat()}.wav".replace(":", "-")
            with open(fn, "wb") as f:
                f.write(audio_bytes)
            st.success("Audio recorded locally (demo). In production, upload to Supabase Storage & save URL.")
            # upload to storage (if bucket configured)
            try:
                # ensure bucket 'answers' exists in your Supabase storage
                up = supabase.storage().from_('answers').upload(fn, open(fn, "rb"))
                public_url = supabase.storage().from_('answers').get_public_url(fn).get('publicURL') or ''
            except Exception as e:
                public_url = ""
            # save answer metadata in DB
            insert = {
                "question_id": q['id'],
                "user_id": current_user,
                "answer_text": answer_text or "",
                "audio_url": public_url
            }
            supabase.table("answers").insert(insert).execute()
            st.info("Saved answer metadata to DB.")
        if st.button("Save text answer"):
            supabase.table("answers").insert({
                "question_id": q['id'],
                "user_id": current_user,
                "answer_text": answer_text,
                "audio_url": None
            }).execute()
            st.success("Answer saved (text).")

# ---------- PRACTICE (writing) TAB ----------
with tabs[2]:
    st.header("3) Practice Writing (4–5 line answers). No Gen-AI.")
    st.write("Write at least 4–5 lines per answer. If you get stuck, click 'Idea' to produce *one* next-line idea (local heuristics). After finishing, click 'Check & Feedback' to get grammar + structure suggestions (no external GenAI).")
    topic_practice = st.text_input("Topic for practice", value="Handling an on-call incident in Kubernetes")
    practice_input = st.text_area("Write your answer here (min 4 lines):", height=220)
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("Idea (give next line suggestion)"):
            # simple idea generator: extract keywords and suggest next line
            words = topic_practice.split()
            idea = f"One step I'd take is to first isolate the faulty component, confirm the failure mode, and then prioritize a rollback or mitigation depending on user impact."
            st.info("Idea (one line): " + idea)
            # user can paste it manually
    with col2:
        if st.button("Check & Feedback"):
            # basic checks
            lines = [l for l in practice_input.splitlines() if l.strip()]
            if len(lines) < 4:
                st.warning("Please write at least 4 lines to practice.")
            else:
                # grammar check
                matches = tool.check(practice_input)
                corrected = language_tool_python.utils.correct(practice_input, matches)
                st.subheader("Corrected version (grammar & style suggestions)")
                st.text_area("Corrected", value=corrected, height=220)
                st.subheader("Feedback (structure)")
                # structure feedback (local heuristics)
                first_sent = lines[0]
                st.write("- Opening: Make your first sentence concise and state the main action.")
                st.write("- Steps: The middle lines should be stepwise actions with brief rationale.")
                st.write("- Closing: End with outcomes or metrics you'd track.")
                # save practice attempt in answers table as metadata (optional)
                supabase.table("answers").insert({
                    "question_id": None,
                    "user_id": current_user,
                    "answer_text": corrected,
                    "audio_url": None
                }).execute()
                st.success("Practice answer corrected and saved for review.")

# ---------- READ / TOPIC TAB ----------
with tabs[3]:
    st.header("4) Read / Topic-based Q&A")
    st.write("Pick a topic or paste a URL. We'll fetch web content, rephrase it (no plagiarism), show sources, and generate question/answer pairs grouped by topic.")
    topic_read = st.text_input("Topic or keyword", value="Kubernetes")
    url_read = st.text_input("Optional: page URL (leave blank to use default sources)")
    n_pair = st.number_input("Number of Q&A pairs to produce", min_value=3, max_value=20, value=5)
    if st.button("Fetch & Prepare"):
        sources = []
        if url_read:
            text = fetch_text_from_url(url_read, max_chars=4000)
            if text:
                sources.append((url_read, text))
        else:
            # naive search: a few canonical pages (in production use search API)
            fallback_urls = [
                "https://kubernetes.io/docs/concepts/overview/what-is-kubernetes/",
                "https://aws.amazon.com/containers/what-is-kubernetes/"
            ]
            for u in fallback_urls:
                t = fetch_text_from_url(u, max_chars=3000)
                if t:
                    sources.append((u, t))
        if not sources:
            st.error("Failed to fetch any sources for this topic.")
        else:
            st.success(f"Fetched {len(sources)} sources. Generating {n_pair} Q&A pairs.")
            qa_list = []
            for i in range(n_pair):
                src_url, text = random.choice(sources)
                q = generate_question_from_text(text, topic_read)
                a = paraphrase_simple(text[:400])  # concise rephrased answer block from source
                # Save in DB as a "question" with source
                supabase.table("questions").insert({
                    "interview_id": st.session_state.get("last_interview_id"),
                    "topic": topic_read,
                    "q_text": q,
                    "source_url": src_url
                }).execute()
                st.markdown(f"**Q{i+1}.** {q}")
                st.caption(src_url)
                st.write(a)
                st.write("---")

# ---------- REVIEW PAST TAB ----------
with tabs[4]:
    st.header("5) Review Past Answers & Activity")
    st.write("View saved answers with timestamps. Data is stored in Supabase (free tier is fine for light usage).")
    try:
        ares = supabase.table("answers").select("id, question_id, answer_text, audio_url, created_at").eq("user_id", current_user).order("created_at", desc=True).limit(50).execute()
        items = ares.data or []
        for it in items:
            st.write(f"**Saved at:** {it.get('created_at')}")
            if it.get('answer_text'):
                st.text_area("answer", value=it.get('answer_text'), height=120)
            if it.get('audio_url'):
                st.audio(it.get('audio_url'))
            st.markdown("---")
    except Exception as e:
        st.write("Could not fetch answers. Check Supabase keys and policies.")

# ---------- PAYMENT / UPGRADE TAB ----------
with tabs[5]:
    st.header("6) Payment / Upgrade ($5)")
    st.write("This app includes a simple $5 checkout flow. For production, create a Stripe account and replace keys in .env.")
    if not STRIPE_PUBLISHABLE or not stripe.api_key:
        st.warning("Stripe keys not configured. See README for setting STRIPE_SECRET_KEY and STRIPE_PUBLISHABLE_KEY in .env.")
    else:
        st.write("Click below to pay $5 to unlock extras (e.g., longer practice, history export).")
        if st.button("Pay $5 (Stripe Checkout)"):
            # Create a stripe Checkout Session (server-side)
            try:
                session = stripe.checkout.Session.create(
                    payment_method_types=['card'],
                    mode='payment',
                    line_items=[{
                        'price_data': {
                            'currency': 'usd',
                            'product_data': {'name': 'AI Interview Coach - Upgrade'},
                            'unit_amount': 500,
                        },
                        'quantity': 1
                    }],
                    success_url='https://your-deployed-app-url/success?session_id={CHECKOUT_SESSION_ID}',
                    cancel_url='https://your-deployed-app-url/cancel'
                )
                st.markdown(f"[Complete payment in browser]({session.url})")
            except Exception as e:
                st.error("Stripe Checkout creation failed: " + str(e))

st.write("-----")
st.caption("Built for demo by ARTy. Replace placeholders with your Supabase & Stripe keys for production. For hosted storage of audio, configure Supabase Storage buckets and policies.")
