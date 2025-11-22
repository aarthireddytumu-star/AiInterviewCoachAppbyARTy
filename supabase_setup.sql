-- Users and auth handled by Supabase Auth
-- Table: interviews
create table if not exists interviews (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null,
  created_at timestamptz default now()
);

-- Table: questions
create table if not exists questions (
  id uuid primary key default gen_random_uuid(),
  interview_id uuid references interviews(id) on delete cascade,
  topic text,
  q_text text,
  source_url text,
  generated_at timestamptz default now()
);

-- Table: answers
create table if not exists answers (
  id uuid primary key default gen_random_uuid(),
  question_id uuid references questions(id) on delete cascade,
  user_id uuid not null,
  answer_text text,
  audio_url text,
  created_at timestamptz default now()
);

-- Table: views or review log
create table if not exists review_logs (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null,
  item_type text, -- 'question' or 'answer'
  item_id uuid,
  viewed_at timestamptz default now()
);
