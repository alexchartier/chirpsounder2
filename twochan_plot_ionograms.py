#!/usr/bin/env python
from detect_chirps import get_metadata
import digital_rf as drf
import time
import os
import sys
import chirp_det as cd
import chirp_config as cc
import scipy.constants as c
import h5py
import glob
import matplotlib.pyplot as plt
import numpy as n
import gc
import matplotlib
matplotlib.use('Agg')


def plot_ionogram(conf, f, f2, normalize_by_frequency=True):
    data = drf.DigitalRFReader(conf.data_dir)
    sample_rate, center_freq = get_metadata(data, conf.channel)
    max_analysis_freq = center_freq + sample_rate / 2
    min_analysis_freq = center_freq - sample_rate / 2

    ho = h5py.File(f, "r")
    h1 = h5py.File(f2, "r")
    t0 = float(n.copy(ho[("t0")]))
    if not "id" in ho.keys():
        return
    cid = int(n.copy(ho[("id")]))  # ionosonde id

    img_fname = "%s/%s/lfm_ionogram-%03d-%1.2f.png" % (
        conf.output_dir, cd.unix2dirname(t0), cid, t0)

    if os.path.exists(img_fname):
        print("Ionogram plot %s already exists. Skipping" % (img_fname))
        ho.close()
        return

    print("Plotting %s rate %1.2f (kHz/s) t0 %1.5f (unix)" %
          (f, float(n.copy(ho[("rate")])) / 1e3, float(n.copy(ho[("t0")]))))

    S1 = n.copy(ho[("S")])          # ionogram frequency-range
    S2 = n.copy(h1[("S")])          # ionogram frequency-range

    """
    # Calculate power 
    S1 = n.abs(S1) ** 2.0
    S2 = n.abs(S2) ** 2.0

    # normalize scale to float16
    S1 = 5e4 * S1 / n.nanmax(S1)
    S2 = 5e4 * S2 / n.nanmax(S2)
    """

    # Select relevant ranges
    S1 = S1[:, ho['ridx']]
    S2 = S2[:, ho['ridx']]

    freqs = n.copy(ho[("freqs")])  # frequency bins
    ranges = n.copy(ho[("ranges")])  # range gates

    """
    # normalize by frequency
    for i in range(S1.shape[0]):
        noise = n.nanmedian(S1[i, :])
        S1[i, :] = (S1[i, :] - noise) / noise
        S2[i, :] = (S2[i, :] - noise) / noise
    S1[S1 <= 0.0] = 1e-3
    S2[S2 <= 0.0] = 1e-3

    """
    max_range_idx = n.argmax(n.max(S1, axis=0))

    """
    dB = n.transpose(10.0 * n.log10(S1))
    dB[n.isnan(dB)] = 0.0
    dB[n.isfinite(dB) != True] = 0.0

    """
    # assume that t0 is at the start of a standard unix second
    # therefore, the propagation time is anything added to a full second

    dt = (t0 - n.floor(t0))
    dr = dt * c.c / 1e3
    range_gates = dr + 2 * ranges / 1e3
    r0 = range_gates[max_range_idx]
    fig = plt.figure(figsize=(1.5 * 8, 1.5 * 6))
    # plt.pcolormesh(freqs/1e6, range_gates, dB, vmin=-3, vmax=30.0, cmap="inferno")
    # plt.pcolormesh(freqs/1E6,range_gates,n.transpose(10.0*n.log10(n.abs(S1*n.conj(S2))[:,::-1])),vmin=-10,vmax=50.0)

    """
    dB = n.transpose(10.0 * n.log10(S1))

    dB[n.isnan(dB)] = 0.0
    dB[n.isfinite(dB) != True] = 0.0

    plt.pcolormesh(freqs / 1E6, range_gates, dB)
    """

    FR = n.transpose((S1 * n.conj(S2))[:, ::-1])
    plt.pcolormesh(freqs/1e6, range_gates, n.angle(FR))

    cb = plt.colorbar()
    cb.set_label("SNR (dB)")
    plt.title("Chirp-rate %1.2f kHz/s t0=%1.5f (unix s)\n%s (UTC)" %
              (float(n.copy(ho[("rate")])) / 1e3, float(n.copy(ho[("t0")])), cd.unix2datestr(float(n.copy(ho[("t0")])))))
    plt.xlabel("Frequency (MHz)")
    plt.ylabel("One-way range offset (km)")
    plt.ylim([dr - conf.max_range_extent / 1e3,
             dr + conf.max_range_extent / 1e3])
    plt.xlim([min_analysis_freq / 1e6, max_analysis_freq / 1e6])
    plt.tight_layout()
    plt.savefig(img_fname)
    fig.clf()
    plt.clf()
    plt.close("all")
    gc.collect()
    ho.close()
    sys.stdout.flush()


if __name__ == "__main__":
    if len(sys.argv) == 2:
        conf = cc.chirp_config(sys.argv[1])
    else:
        conf = cc.chirp_config()

    if conf.realtime:
        while True:
            fl = glob.glob("%s/*/lfm*.h5" % (conf.output_dir))
            fl.sort()
            for f in fl:
                plot_ionogram(conf, f)
            time.sleep(10)
    else:
        fl = glob.glob("%s/*[0-9]/lfm*.h5" % (conf.output_dir))
        for f in fl:
            f2 = glob.glob(os.path.join("%s/*_b/" %
                           (conf.output_dir), os.path.basename(f)))[0]
            plot_ionogram(conf, f, f2)
