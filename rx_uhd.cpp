//
// This is based on one of the UHD driver examples.
// Juha Vierinen, 2021
//
// Copyright 2010-2011 Ettus Research LLC
// Copyright 2018 Ettus Research, a National Instruments Company
//
// SPDX-License-Identifier: GPL-3.0-or-later
//

#include <uhd/usrp/multi_usrp.hpp>
#include <uhd/utils/safe_main.hpp>
#include <boost/algorithm/string.hpp>
#include <boost/format.hpp>
#include <boost/program_options.hpp>
#include <complex>
#include <chrono>
#include <thread>
#include <iostream>
#include <unistd.h>
#include <filesystem>
#include <digital_rf/digital_rf.h>

namespace po = boost::program_options;
namespace fs = std::filesystem;
namespace ch = std::chrono;

double set_internal_time()
{
    // Sync the USRP with system time
    auto curr_time = ch::system_clock::now();
    auto sec_since_epoch(
        ch::duration_cast<ch::seconds>(curr_time.time_since_epoch()));
    auto ns_since_epoch(ch::duration_cast<ch::nanoseconds>(
        curr_time.time_since_epoch() - sec_since_epoch));

    std::string whole_string = std::to_string(sec_since_epoch.count());
    std::string frac_string = std::to_string(ns_since_epoch.count());
    double curr_time_sec = std::stod(whole_string + "." + frac_string);

    return curr_time_sec;
}

int UHD_SAFE_MAIN(int argc, char *argv[])
{
    // Variables to be set by po
    std::string args;
    double rate;
    double freq;
    std::string ref_source;
    std::string time_source;
    std::string subdev;
    std::string channel_list;
    std::string outdir;

    // Setup the program options
    po::options_description desc("Allowed options");
    // clang-format off
    desc.add_options()
        ("help", "help message")
        ("args", po::value<std::string>(&args)->default_value("recv_buff_size=500000000"), "multi uhd device address args")
        ("rate", po::value<double>(&rate)->default_value(25e6), "rate of incoming samples in Hz")
        ("freq", po::value<double>(&freq)->default_value(12.5e6), "RF center frequency in Hz")
        ("ref-source", po::value<std::string>(&ref_source)->default_value("internal"), "reference source (internal, external, mimo, gpsdo)")
        ("time-source", po::value<std::string>(&time_source)->default_value(""), "the time source (gpsdo, external) or blank for default")
        ("subdevice", po::value<std::string>(&subdev)->default_value("A:A"), "subdevice specification")
        ("channels", po::value<std::string>(&channel_list)->default_value("0"), "which channel(s) to use (specify \"0\", \"1\", \"0,1\", etc)")
        ("outdir", po::value<std::string>(&outdir)->default_value("/dev/shm/hf25"), "output directory")
    ;
    // clang-format on
    po::variables_map vm;
    po::store(po::parse_command_line(argc, argv, desc), vm);
    po::notify(vm);

    // Print the help message
    if (vm.count("help"))
    {
        std::cout << boost::format("UHD RX %s") % desc << std::endl;
        return ~0;
    }

    // Create a USRP device
    printf("\nCreating the usrp device with: %s...\n", args.c_str());
    uhd::usrp::multi_usrp::sptr usrp = uhd::usrp::multi_usrp::make(args);

    // Always select the subdevice first, the channel mapping affects the other settings
    usrp->set_rx_subdev_spec(subdev);

    printf("Using Device:\n %s", usrp->get_pp_string().c_str());
    printf("%s\n", usrp->get_rx_subdev_spec().to_pp_string().c_str());

    // Detect which channels to use
    std::vector<std::string> channel_strings;
    std::vector<size_t> channel_nums;
    boost::split(channel_strings, channel_list, boost::is_any_of("\"',"));
    for (size_t ch = 0; ch < channel_strings.size(); ch++)
    {
        size_t chan = std::stoi(channel_strings[ch]);
        if (chan >= usrp->get_rx_num_channels())
            throw std::runtime_error("Invalid channel(s) specified.");
        else
            channel_nums.push_back(std::stoi(channel_strings[ch]));
    }

    // Turn on correctors
    usrp->set_rx_dc_offset(true);
    usrp->set_rx_iq_balance(true);

    // Lock mboard clocks
    usrp->set_clock_source(ref_source);
    if (!time_source.empty())
        usrp->set_time_source(time_source);

    if (time_source.empty())
    {
        usrp->set_time_now(uhd::time_spec_t(set_internal_time()));
    }
    else if (time_source.compare("gpsdo") == 0)
    {
        printf("Waiting for lock\n");
        // Wait for GPS lock
        bool gps_locked = usrp->get_mboard_sensor("gps_locked").to_bool();
        while (gps_locked == false)
        {
            // sleep for 10 seconds
            std::this_thread::sleep_for(std::chrono::seconds(10));
            gps_locked = usrp->get_mboard_sensor("gps_locked").to_bool();
            printf("No GPS lock, waiting for lock\n");
        }

        const time_t gps_time = usrp->get_mboard_sensor("gps_time").to_int();
        usrp->set_time_next_pps(uhd::time_spec_t(static_cast<int64_t>(gps_time + 1)));

        // Wait for it to apply
        // The wait is 2 seconds because N-Series has a known issue where
        // the time at the last PPS does not properly update at the PPS edge
        // when the time is actually set.
        std::this_thread::sleep_for(std::chrono::seconds(2));

        uhd::time_spec_t time_last_pps = usrp->get_time_last_pps();
        printf("USRP time now %1.4f USRP last pps %1.4f\n",
               usrp->get_time_now().get_real_secs(), time_last_pps.get_real_secs());
    }

    // set the RX sample rate
    printf("Setting RX Rate: %f Msps...\n", rate / 1e6);
    usrp->set_rx_rate(rate);

    // Set the RX center freq
    printf("Setting RX Freq: %f MHz...\n", freq / 1e6);
    usrp->set_rx_freq(uhd::tune_request_t(freq));

    // Create a receive streamer
    uhd::stream_args_t stream_args("sc16", "sc16"); // complex shorts
    stream_args.channels = channel_nums;
    uhd::rx_streamer::sptr rx_stream = usrp->get_rx_stream(stream_args);
    auto num_channels = rx_stream->get_num_channels();

    // Setup streaming
    auto start_time = usrp->get_time_now().get_real_secs() + 2.0;
    printf("Streaming will start at: %f\n", start_time);

    // Create output dirs for up to two channels
    std::vector<fs::path> ch_dir_list = {
        fs::path(outdir) / fs::path("cha"),
        fs::path(outdir) / fs::path("chb")};
    std::error_code ec;
    fs::create_directory(ch_dir_list[0], ec);
    if (ec.value() != 0)
        throw std::runtime_error("Directory could not be created");
    if (num_channels == 2)
    {
        fs::create_directory(ch_dir_list[1], ec);
        if (ec.value() != 0)
            throw std::runtime_error("Directory could not be created");
    }

    // Init DRF for up to two channels
    std::vector<Digital_rf_write_object *> drf_objects = {NULL, NULL};

    // DRF writing parameters
    uint64_t sample_rate_numerator = rate;
    uint64_t sample_rate_denominator = 1;
    uint64_t global_start_index =
        (uint64_t)((uint64_t)start_time * (long double)sample_rate_numerator / sample_rate_denominator);
    uint64_t subdir_cadence = 3600;
    uint64_t millseconds_per_file = 1000;
    int compression_level = 0; // no compression
    int checksum = 0;          // no checksum
    int is_complex = 1;        // complex values
    int is_continuous = 1;     // continuous data written
    int num_subchannels = 1;   // one subchannel
    int marching_periods = 0;  // no marching periods
    char uuid[100] = "6HZWCRzdQYRrvNwkikPsxw0nkg2or";

    drf_objects[0] = digital_rf_create_write_hdf5(strdup(ch_dir_list[0].string().c_str()),
                                                  H5T_NATIVE_SHORT,
                                                  subdir_cadence,
                                                  millseconds_per_file,
                                                  global_start_index,
                                                  sample_rate_numerator,
                                                  sample_rate_denominator,
                                                  uuid,
                                                  compression_level,
                                                  checksum,
                                                  is_complex,
                                                  num_subchannels,
                                                  is_continuous,
                                                  marching_periods);
    if (!drf_objects[0])
    {
        printf("DRF data objects failed to be created\n");
        exit(EXIT_FAILURE);
    }
    if (num_channels == 2)
    {
        drf_objects[1] = digital_rf_create_write_hdf5(strdup(ch_dir_list[1].string().c_str()),
                                                      H5T_NATIVE_SHORT,
                                                      subdir_cadence,
                                                      millseconds_per_file,
                                                      global_start_index,
                                                      sample_rate_numerator,
                                                      sample_rate_denominator,
                                                      uuid,
                                                      compression_level,
                                                      checksum,
                                                      is_complex,
                                                      num_subchannels,
                                                      is_continuous,
                                                      marching_periods);
        if (!drf_objects[1])
        {
            printf("DRF data objects failed to be created\n");
            exit(EXIT_FAILURE);
        }
    }

    // Issue USRP stream command
    uhd::stream_cmd_t stream_cmd(uhd::stream_cmd_t::STREAM_MODE_START_CONTINUOUS);
    stream_cmd.stream_now = false;
    stream_cmd.time_spec = uhd::time_spec_t(start_time);
    rx_stream->issue_stream_cmd(stream_cmd);

    // Allocate buffers to receive with samples (one buffer per channel)
    const size_t samps_per_buff = rx_stream->get_max_num_samps();
    std::vector<std::vector<std::complex<short>>> buffs(
        num_channels, std::vector<std::complex<short>>(samps_per_buff));

    // Create a vector of pointers to point to each of the channel buffers
    std::vector<std::complex<short> *> buff_ptrs;
    for (size_t i = 0; i < buffs.size(); i++)
        buff_ptrs.push_back(&buffs[i].front());

    size_t num_rx_samps;
    uhd::rx_metadata_t md;
    std::vector<uint64_t> vector_leading_edge_indexes(2);
    while (true)
    {
        num_rx_samps = rx_stream->recv(buff_ptrs, samps_per_buff, md);

        if (num_rx_samps)
        {
            digital_rf_write_hdf5(drf_objects[0], vector_leading_edge_indexes[0], buff_ptrs[0], num_rx_samps);
            vector_leading_edge_indexes[0] += num_rx_samps;

            if (num_channels == 2)
            {
                digital_rf_write_hdf5(drf_objects[1], vector_leading_edge_indexes[1], buff_ptrs[1], num_rx_samps);
                vector_leading_edge_indexes[1] += num_rx_samps;
            }
        }
    }

    return EXIT_SUCCESS;
}
