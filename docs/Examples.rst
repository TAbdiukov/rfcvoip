Examples
########

These examples focus on the high-level ``VoIPPhone`` and ``VoIPCall`` API.
The lower-level SIP and RTP modules are documented separately.

Basic setup
***********

rfcvoip uses a callback for incoming calls. The callback receives a
``VoIPCall`` instance. A phone with no callback automatically rejects incoming
calls as busy.

The following phone answers an incoming call and immediately hangs up:

.. code-block:: python

  from rfcvoip.VoIP import InvalidStateError, VoIPPhone


  def answer(call):
      try:
          call.answer()
          call.hangup()
      except InvalidStateError:
          pass


  if __name__ == "__main__":
      phone = VoIPPhone(
          "sip.example.net",
          5060,
          "1000",
          "password",
          myIP="192.0.2.10",
          callCallback=answer,
      )
      phone.start()
      input("Press enter to disable the phone")
      phone.stop()

Use keyword arguments for optional SIP features:

.. code-block:: python

  phone = VoIPPhone(
      "sip.example.net",
      5060,
      "1000",
      "password",
      myIP="192.0.2.10",
      auth_username="1000-auth",
      proxy="sip:proxy.example.net;transport=tcp",
      transport="tcp",
      callCallback=answer,
  )

Playing an announcement
***********************

The public audio API accepts unsigned 8-bit linear mono bytes. The negotiated
codec chooses the default sample rate. Use ``call.audio_frame_size()`` or
``call.audio_format()`` when you need to calculate frame sizes dynamically.

This example plays a WAV file, waits until playback should be finished, and
then hangs up. The loop exits early if the remote party hangs up or the phone
is stopped.

.. code-block:: python

  import time
  import wave

  from rfcvoip.VoIP import CallState, InvalidStateError, VoIPPhone


  def answer(call):
      try:
          with wave.open("announcement.wav", "rb") as wav:
              data = wav.readframes(wav.getnframes())
              frames = wav.getnframes()
              sample_rate = wav.getframerate()

          call.answer()
          call.write_audio(data)

          stop = time.time() + (frames / sample_rate)
          while time.time() <= stop and call.state == CallState.ANSWERED:
              time.sleep(0.1)

          call.hangup()
      except InvalidStateError:
          pass
      except Exception:
          if call.state == CallState.ANSWERED:
              call.hangup()


  if __name__ == "__main__":
      phone = VoIPPhone(
          "sip.example.net",
          5060,
          "1000",
          "password",
          myIP="192.0.2.10",
          callCallback=answer,
      )
      phone.start()
      input("Press enter to disable the phone")
      phone.stop()

For the simplest legacy G.711 flow, use 8000 Hz, 8-bit, mono WAV audio. If you
enable wideband codecs, confirm that the audio sample rate you provide matches
``call.audio_format()["sample_rate"]`` or pass ``audio_sample_rate=8000`` to
``VoIPPhone`` to keep a fixed 8000 Hz application audio pipeline.

IVR and DTMF
************

DTMF received through RTP telephone-event is stored on the call and can be read
with ``get_dtmf``. The method defaults to one character and returns an empty
string when no key is available.

.. code-block:: python

  import time
  import wave

  from rfcvoip.VoIP import CallState, InvalidStateError, VoIPPhone


  def answer(call):
      try:
          with wave.open("prompt.wav", "rb") as wav:
              prompt = wav.readframes(wav.getnframes())

          call.answer()
          call.write_audio(prompt)

          while call.state == CallState.ANSWERED:
              digit = call.get_dtmf()
              if digit == "1":
                  call.write_audio(b"\x80" * call.audio_frame_size())
                  call.hangup()
              elif digit == "2":
                  call.send_dtmf("9")
                  call.hangup()
              time.sleep(0.1)
      except InvalidStateError:
          pass
      except Exception:
          if call.state == CallState.ANSWERED:
              call.hangup()


  if __name__ == "__main__":
      phone = VoIPPhone(
          "sip.example.net",
          5060,
          "1000",
          "password",
          myIP="192.0.2.10",
          callCallback=answer,
      )
      phone.start()
      input("Press enter to disable the phone")
      phone.stop()

Outbound calls
**************

``VoIPPhone.call`` originates a call and returns a ``VoIPCall``. The call may
start in ``DIALING`` or ``RINGING``. When a 200 OK with compatible SDP arrives,
rfcvoip sends ACK, creates RTP clients, and moves the call to ``ANSWERED``.

.. code-block:: python

  import time

  from rfcvoip.VoIP import CallState, VoIPPhone


  phone = VoIPPhone(
      "sip.example.net",
      5060,
      "1000",
      "password",
      myIP="192.0.2.10",
  )
  phone.start()

  call = phone.call("1001")
  while call.state in (CallState.DIALING, CallState.RINGING):
      time.sleep(0.05)

  if call.state == CallState.ANSWERED:
      call.write_audio(b"\x80" * call.audio_frame_size())
      call.send_dtmf("123#")
      call.hangup()

  phone.stop()

Codec priorities
****************

Codec order affects both local SDP offers and the selected RTP codec when a
remote endpoint advertises more than one compatible payload.

.. code-block:: python

  from rfcvoip import RTP
  from rfcvoip.VoIP import VoIPPhone


  phone = VoIPPhone(
      "sip.example.net",
      5060,
      "1000",
      "password",
      myIP="192.0.2.10",
      codec_priorities={
          RTP.PayloadType.PCMU: 1000,
          RTP.PayloadType.PCMA: 900,
      },
      audio_sample_rate=8000,
  )

You can also adjust priorities after construction:

.. code-block:: python

  phone.set_codec_priority(RTP.PayloadType.PCMU, 1200)
  phone.reset_codec_priorities()

Telemetry
*********

Telemetry helpers expose codec negotiation, SIP auth, and call state without
including passwords or digest responses.

.. code-block:: python

  from rfcvoip import Telemetry


  print(Telemetry.report(phone))
  print(Telemetry.get(phone, "auth.last_digest.algorithm", default="none"))

SIP OPTIONS can be used to inspect a remote endpoint when the server returns
SDP in the OPTIONS response:

.. code-block:: python

  report = Telemetry.phone_codec_report(phone, target="1001")
  for codec in report["remote"]:
      print(codec["name"], codec["supported"])