import Image from 'next/image'

import wordmark from '@/assets/wordmark.png'
import Downloads from '@/components/Downloads'
import Feature from '@/components/Feature'
import HeroClip from '@/components/HeroClip'
import HeroDownload from '@/components/HeroDownload'
import { ReleaseProvider } from '@/components/ReleaseProvider'
import { FALLBACK, fetchRelease, REPO_URL } from '@/lib/release'
import { screens } from '@/lib/screens'

export default async function Home() {
  return (
    <ReleaseProvider initial={await fetchRelease(FALLBACK)}>
      <a className="skip" href="#main">
        Skip to content
      </a>
      <header className="site-head">
        <div className="wrap">
          <a href="#main" className="brand">
            <Image src={wordmark} alt="Colloquia" priority />
          </a>
          <nav aria-label="Page sections">
            <a href="#features">Features</a>
            <a href="#privacy">Privacy</a>
            <a href={REPO_URL}>GitHub</a>
            <a className="button button-small" href="#download">
              Download
            </a>
          </nav>
        </div>
      </header>

      <main id="main">
        <section className="hero">
          <div className="wrap">
            <h1>Rehearse like someone’s watching.</h1>
            <p className="lead">
              Colloquia is a free desktop app for practicing presentations. It watches your posture
              and hands, listens to your voice, follows your script, and tells you what to fix while
              there’s still time to fix it.
            </p>
            <HeroDownload />
            <HeroClip />
          </div>
        </section>

        <section className="purpose">
          <div className="wrap">
            <h2>Practicing alone leaves you guessing.</h2>
            <div className="purpose-text">
              <p>
                You can’t hear your own “um”s, see your shoulders drop, or notice that you rushed
                the last third. A mirror won’t tell you, and asking a friend to sit through the
                fifth run gets old.
              </p>
              <p>
                Colloquia gives every run an audience. It was made for students, and for anyone with
                a talk, pitch or defense coming up.
              </p>
            </div>
          </div>
        </section>

        <div id="features">
          <section className="phase" aria-labelledby="before">
            <div className="wrap">
              <header className="phase-head">
                <p className="phase-time" aria-hidden="true">
                  00:00
                </p>
                <div>
                  <h2 id="before">Before you start</h2>
                  <p>A minute of setup, so you’re measured against your own normal.</p>
                </div>
              </header>
              <div className="features">
                <Feature
                  title="Tuned to your voice"
                  screen={screens.calibration}
                  alt="Voice calibration page with the saved pitch and volume reference and their graphs."
                >
                  <p>
                    Read one sentence aloud, and Colloquia learns your normal pitch and volume. Your
                    resting posture is measured at the start of every session.
                  </p>
                </Feature>
                <Feature
                  title="Only the alerts you want"
                  screen={screens.settings}
                  alt="Settings page listing alert rules with their points, and webcam display options."
                >
                  <p>
                    Turn each check on or off, set how sensitive it is, and see how many points it’s
                    worth. Anything you turn off is left out of your score.
                  </p>
                </Feature>
              </div>
            </div>
          </section>

          <section className="phase" aria-labelledby="during">
            <div className="wrap">
              <header className="phase-head">
                <p className="phase-time" aria-hidden="true">
                  00:12
                </p>
                <div>
                  <h2 id="during">While you present</h2>
                  <p>Short cues on screen, only when something drifts.</p>
                </div>
              </header>
              <div className="features">
                <Feature
                  wide
                  title="Live alerts"
                  screen={screens.alert}
                  alt="The presenter’s video with two live alerts in the corner."
                >
                  <p>
                    Looking down, tilting, rushing, going quiet or leaving your script: a short cue
                    appears on your video. Choose a banner, a small label, or no cues at all.
                  </p>
                  <ul className="cues">
                    <li>Look up</li>
                    <li>Slow your hands</li>
                    <li>Speak up</li>
                    <li>Skipped ahead</li>
                  </ul>
                </Feature>
                <Feature
                  wide
                  title="Posture, eyes and hands"
                  screen={screens.skeleton}
                  alt="Camera preview with the tracked skeleton drawn over the presenter and live posture stats."
                >
                  <p>
                    Shoulder tilt, eye level, framing, and hands that are hidden, frozen, too fast
                    or stuck in a loop. Show the tracking over your video, or keep the picture
                    clean.
                  </p>
                </Feature>
                <Feature
                  wide
                  title="Voice and pace"
                  screen={screens.voice}
                  alt="Live graphs of voice pitch, microphone volume and speaking pace."
                >
                  <p>
                    Pitch, volume and words per minute, live against your own baseline and the pace
                    you need to finish on time.
                  </p>
                </Feature>
                <Feature
                  wide
                  title="Every word, every “um”"
                  screen={screens.transcript}
                  alt="Live transcript with filler words highlighted and counted."
                >
                  <p>A live transcript that counts and highlights um, uh, like, you know and so.</p>
                </Feature>
                <Feature
                  wide
                  title="Follows your script"
                  screen={screens.script}
                  alt="Script split into parts with the current part highlighted."
                >
                  <p>
                    Paste your script or attach a PDF. Colloquia shows which part you’re on, and
                    warns you when you skip ahead, go back, get stuck or drift off topic. You don’t
                    have to say it word for word.
                  </p>
                </Feature>
              </div>
            </div>
          </section>

          <section className="phase" aria-labelledby="after">
            <div className="wrap">
              <header className="phase-head">
                <p className="phase-time" aria-hidden="true">
                  End
                </p>
                <div>
                  <h2 id="after">When you’re done</h2>
                  <p>A score you can explain, and the moments behind it.</p>
                </div>
              </header>
              <div className="features">
                <Feature
                  wide
                  title="A score out of 100, broken down"
                  screen={screens.results}
                  alt="Practice results with a percentage score, points per category and feedback."
                >
                  <p>
                    Filler words, voice tone, pace, body language, script accuracy and time limit.
                    Each has its own points, followed by what to work on next. Checks that couldn’t
                    be measured don’t count against you.
                  </p>
                </Feature>
                <Feature
                  title="Watch it back"
                  screen={screens.playback}
                  alt="Recording playback with markers on the timeline."
                >
                  <p>
                    Markers on the timeline take you to each moment, so you can see what the alert
                    saw.
                  </p>
                </Feature>
                <Feature
                  title="See yourself improve"
                  screen={screens.progress}
                  alt="Home page with a chart of practice scores and a list of attempts."
                >
                  <p>Keep a folder for each presentation and follow your score from run to run.</p>
                </Feature>
                <Feature
                  wide
                  title="Already have a recording?"
                  screen={screens.upload}
                  alt="Upload page with a selected recording, a script and a time limit."
                >
                  <p>
                    Upload a video or audio file and it’s graded with the same scoring. Audio files
                    skip the body language checks.
                  </p>
                </Feature>
              </div>
            </div>
          </section>
        </div>

        <section className="privacy" id="privacy">
          <div className="wrap">
            <h2>Your rehearsals stay on your computer.</h2>
            <ul>
              <li>
                <strong>Analyzed locally.</strong> Your video, voice and script are processed on
                your own machine, not on a server.
              </li>
              <li>
                <strong>No account.</strong> Download it, open it, practice.
              </li>
              <li>
                <strong>Yours to delete.</strong> Recordings are saved on your computer, and you can
                delete a video and keep its score.
              </li>
              <li>
                <strong>Online once.</strong> The first launch downloads about 370 MB of speech and
                language models. After that it works offline.
              </li>
            </ul>
          </div>
        </section>

        <section className="get" id="download">
          <div className="wrap">
            <h2>Download Colloquia</h2>
            <p className="lead">
              Free. The links download the app directly. Because it isn’t signed with a paid
              certificate, your system asks before opening it the first time.
            </p>
            <Downloads />
          </div>
        </section>
      </main>

      <footer className="site-foot">
        <div className="wrap">
          <Image src={wordmark} alt="" />
          <p>
            Made for Codejam 16. <a href={REPO_URL}>Source code</a> ·{' '}
            <a href={`${REPO_URL}/releases`}>All releases</a>
          </p>
          <p>
            Screenshots are from the app. Presenter footage by{' '}
            <a href="https://mixkit.co/free-stock-video/man-talking-head-on-on-a-chroma-background-28287/">
              Mixkit
            </a>
            ; the speech is synthetic.
          </p>
        </div>
      </footer>
    </ReleaseProvider>
  )
}
