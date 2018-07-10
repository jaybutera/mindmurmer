import os
import logging
import numpy as np
import swmixer
import time
import argparse

from collections import defaultdict
from aubio import source, sink, pvoc, cvec, tempo, unwrap2pi, float_type
from numpy import median, diff

# I cannot figure out the scale of these, not seconds.. these values seem right
FADEOUT_AMOUNT = 10
FADEIN_AMOUNT = 300


class MindMurmurAudioController(object):
	""" Non-blocking Audio controller to mix tracks in a given folder

	NOTE: Currently supporting only tracks with sample rate of 16,000 hz

	TODO(AmirW): make this non blocking :)
	"""
	def __init__(self, audio_folder, heartbeat_folder, sample_rate=16000):
		self._validate_audio_files(audio_folder, heartbeat_folder)

		self.audio_folder = audio_folder
		self.heartbeat_folder = heartbeat_folder
		self.sample_rate = sample_rate
		self.current_playing_track = None
		self.tracks_to_mode_map = defaultdict(list)
		self.heartbeats_to_bpm_map = defaultdict(list)
		self.current_playing_sound = None
		self.sound_snd = None

		swmixer.init(samplerate=sample_rate, chunksize=1024, stereo=True)
		swmixer.start()

		self.tracks_to_mode_map = self._map_tracks_by_rate(self.audio_folder)
		self.heartbeats_to_bpm_map = self._map_tracks_by_rate(self.heartbeat_folder)

	def _validate_audio_files(self, audio_folder, heartbeat_folder):
		""" Validate "audio_folder" and "heartbeat_folder" are an actual folders

		:param audio_folder: path to tracks folder on system
		:param heartbeat_folder: path to sound file on system
		"""
		if not os.path.isdir(audio_folder):
			raise ValueError("{folder} folder does not exists on system".format(folder=audio_folder))

		if not os.path.isdir(heartbeat_folder):
			raise ValueError("{heartbeat_folder} folder does not exists on system".format(
				heartbeat_folder=heartbeat_folder))

		logging.info("using audio folder: {audio_folder}".format(audio_folder=audio_folder))
		logging.info("using heartbeat folder: {heartbeat_folder}".format(heartbeat_folder=heartbeat_folder))

	def _map_tracks_by_rate(self, tracks_folder):
		""" Iterate over audio_folder and get bpm for each track and map songs to BOM

		:param tracks_folder: the folder to map
		:return: a dict of BPM to track
		"""
		tracks_to_mode_map = defaultdict(list)

		for track_filename in os.listdir(tracks_folder):
			bpm = MindMurmurAudioController.get_track_bmp(os.path.join(tracks_folder, track_filename))

			if bpm == 0:
				bpm_from_filename = int(track_filename.split(".")[0].split("_")[-1])
				bpm = bpm_from_filename

			tracks_to_mode_map[bpm].append(track_filename)
			logging.info("track \"{track}\" has BPM of: {bpm}".format(track=track_filename, bpm=bpm))

		return tracks_to_mode_map


	def _get_track_for_mode(self, mode):
		""" Get track from folder by normalizing mode over list of BPMs

		:param mode: a number between 0 and 5, 0 being the lightest and 5 the heaviest

		:return: track filename matching the mode
		"""
		# TODO(AmirW): normalize this
		if 0 <= mode < len(self.tracks_to_mode_map.keys()):
			sorted_keys = sorted(self.tracks_to_mode_map.keys())
			return self.tracks_to_mode_map[sorted_keys[mode]][0]
		else:
			raise ValueError("{mode} mode is not in the audio folder".format(mode=mode))

	def _mix_track(self, track_filename):
		""" Mix a track with path "track filename" to play. If another track is playing, fade it out, and fade this in

		:param track_filename: track filename to fade in
		"""
		track_full_path = os.path.join(self.audio_folder, track_filename)
		fadein_time = 0

		if self.current_playing_track is not None:
			logging.info("fading out current track to end in {fadeout} seconds".format(fadeout=FADEOUT_AMOUNT))
			self.current_playing_track.set_volume(0, fadetime=self.sample_rate * FADEOUT_AMOUNT)
			fadein_time = self.sample_rate * FADEIN_AMOUNT
			logging.info("set fade")

		# fade in of one second
		snd = swmixer.Sound(track_full_path)
		chan = snd.play(fadein=fadein_time)
		self.current_playing_track = chan
		logging.info("starting playing {track}".format(track=track_filename))

	def mix_track(self, mode):
		""" Gracefully mix in a track mapped to "mode". If another track is already playing - fade it out

		:param mode: a target value to choose a track by
		"""
		# fetch the track mapped to "mode"
		track_filename = self._get_track_for_mode(mode)
		self._mix_track(track_filename)

	def play_heartbeat_with_bpm(self, bpm):
		""" Play with "bpm" beats per minute by calling starting a self calling timer

		:param bpm:
		:return:
		"""
		# (TODO: AmirW) we'll need to pre-sample the heartbeat rate at different BPMs (60 BPM - sound lasts a second, 120
		# sound lasts 0.5 a second

		if self.current_playing_sound is not None:
			self.current_playing_sound.set_volume(0)

		# Load the correct sound with BPM and repeat until called with another BPM
		heartbeat_track = self.heartbeats_to_bpm_map.get(bpm, [None])[0]
		if heartbeat_track is None:
			raise ValueError("we don't have that rate of heartbeat ({!s})".format(bpm))

		sound_snd = swmixer.Sound(os.path.join(self.heartbeat_folder, heartbeat_track))
		self.current_playing_sound = sound_snd.play(volume=1.3, loops=60 * 10)
		logging.info("starting playing heartbeat at {bpm} BPM".format(bpm=bpm))

	@staticmethod
	def get_track_bmp(path, params=None):
		""" Calculate the beats per minute (bpm) of a given file.
			path: path to the file
			param: dictionary of parameters
		"""
		if params is None:
			params = {}

		# default:
		sample_rate, win_s, hop_s = 16000, 1024, 512

		if 'mode' in params:
			if params.mode in ['super-fast']:
			# super fast
				sample_rate, win_s, hop_s = 4000, 128, 64
			elif params.mode in ['fast']:
			# fast
				sample_rate, win_s, hop_s = 8000, 512, 128
			elif params.mode in ['default']:
				pass
			else:
				print("unknown mode {:s}".format(params.mode))

		# manual settings
		if 'samplerate' in params:
			sample_rate = params.samplerate
		if 'win_s' in params:
			win_s = params.win_s
		if 'hop_s' in params:
			hop_s = params.hop_s

		s = source(path, sample_rate, hop_s)
		sample_rate = s.samplerate
		o = tempo("specdiff", win_s, hop_s, sample_rate)
		# List of beats, in samples
		beats = []
		# Total number of frames read
		total_frames = 0

		while True:
			samples, read = s()
			is_beat = o(samples)
			if is_beat:
				this_beat = o.get_last_s()
				beats.append(this_beat)
			# if o.get_confidence() > .2 and len(beats) > 2.:
			#    break
			total_frames += read
			if read < hop_s:
				break

		def beats_to_bpm(beats, path):
			# if enough beats are found, convert to periods then to bpm
			if len(beats) > 1:
				if len(beats) < 4:
					print("few beats found in {:s}".format(path))
				bpms = 60. / diff(beats)
				return median(bpms)
			else:
				logging.info("not enough beats found in {:s}".format(path))
				return 0

		return beats_to_bpm(beats, path)

	@staticmethod
	def alter_track_tempo(source_filename, output_filename, rate, sample_rate=0):
		win_s = 512
		hop_s = win_s // 8  # 87.5 % overlap

		warm_up = win_s // hop_s - 1
		source_in = source(source_filename, sample_rate, hop_s)
		sample_rate = source_in.samplerate
		p = pvoc(win_s, hop_s)

		sink_out = sink(output_filename, sample_rate)

		# excepted phase advance in each bin
		phi_advance = np.linspace(0, np.pi * hop_s, win_s / 2 + 1).astype(float_type)

		old_grain = cvec(win_s)
		new_grain = cvec(win_s)

		block_read = 0
		interp_read = 0
		interp_block = 0

		while True:
			samples, read = source_in()
			cur_grain = p(samples)

			if block_read == 1:
				phas_acc = old_grain.phas

			# print "block_read", block_read
			while True and (block_read > 0):
				if interp_read >= block_read:
					break
				# print "`--- interp_block:", interp_block,
				# print 'at orig_block', interp_read, '<- from', block_read - 1, block_read,
				# print 'old_grain', old_grain, 'cur_grain', cur_grain
				# time to compute interp grain
				frac = 1. - np.mod(interp_read, 1.0)

				# compute interpolated frame
				new_grain.norm = frac * old_grain.norm + (1. - frac) * cur_grain.norm
				new_grain.phas = phas_acc

				# psola
				samples = p.rdo(new_grain)
				if interp_read > warm_up:  # skip the first frames to warm up phase vocoder
					# write to sink
					sink_out(samples, hop_s)

				# calculate phase advance
				dphas = cur_grain.phas - old_grain.phas - phi_advance
				# unwrap angle to [-pi; pi]
				dphas = unwrap2pi(dphas)
				# cumulate phase, to be used for next frame
				phas_acc += phi_advance + dphas

				# prepare for next interp block
				interp_block += 1
				interp_read = interp_block * rate
				if interp_read >= block_read:
					break

			# copy cur_grain to old_grain
			old_grain.norm = np.copy(cur_grain.norm)
			old_grain.phas = np.copy(cur_grain.phas)

			# until end of file
			if read < hop_s: break
			# increment block counter
			block_read += 1

		for t in range(warm_up + 2):  # purge the last frames from the phase vocoder
			new_grain.norm[:] = 0
			new_grain.phas[:] = 0
			samples = p.rdo(new_grain)
			sink_out(samples, read if t == warm_up + 1 else hop_s)

		# just to make sure
		source_in.close()
		sink_out.close()

		format_out = "read {:d} blocks from {:s} at {:d}Hz and rate {:f}, wrote {:d} blocks to {:s}"
		logging.info(format_out.format(block_read, source_filename, sample_rate, rate,
									   interp_block, output_filename))


if __name__ == "__main__":
	parser = argparse.ArgumentParser(description='Process some integers.')
	parser.add_argument('--audio_folder', dest='audio_folder', help="The folder with the tracks")
	parser.add_argument('--heartbeat_folder', dest='heartbeat_folder', help="The folder of the heartbeats")
	parser.add_argument('--create_multi_tempo_versions', dest='create_multi_tempo_versions', action='store_true',
						default=False, help='create multi tempo versions of each track in --audio_folder')

	log_format = ('%(asctime)s %(filename)s %(lineno)s %(process)d %(levelname)s: %(message)s')
	log_level = logging.INFO
	logging.basicConfig(format=log_format, level=log_level)

	args = parser.parse_args()

	ac = MindMurmurAudioController(args.audio_folder, args.heartbeat_folder)

	if args.create_multi_tempo_versions:
		for bpm in range(10, 185, 5):
			scale = bpm / 60.0
			output_filename = "{filename}_{bpm}.{file_suffix}".format(
				bpm=bpm, filename=args.sound_filename.split(".")[0], file_suffix=args.sound_filename.split(".")[-1])
			logging.info("rendering sound with bpm: {!s}".format(bpm))
			MindMurmurAudioController.alter_track_tempo(args.sound_filename, output_filename, scale)
	else:
		logging.info("loading HB with 45 BPM")
		ac.play_heartbeat_with_bpm(45)
		time.sleep(5)

		logging.info("mixing in track 1")
		ac.mix_track(0)
		logging.info("mixed in track 1")

		time.sleep(5)
		logging.info("loading HB with 60 BPM")
		ac.play_heartbeat_with_bpm(60)

		time.sleep(5)

		logging.info("mixing in track 2")
		ac.mix_track(1)
		logging.info("mixed in track 2")

		time.sleep(5)

		logging.info("loading HB with 70 BPM")
		ac.play_heartbeat_with_bpm(70)

		time.sleep(5)

		logging.info("mixing in track 1")
		ac.mix_track(0)
		logging.info("mixed in track 1")

		logging.info("loading HB with 60 BPM")
		ac.play_heartbeat_with_bpm(60)

		time.sleep(10)