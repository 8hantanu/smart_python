'''
    First created 2017-09-15
    Sophie A. Murray

    Python Version:    Python 3.6.1 |Anaconda custom (x86_64)| (default, May 11 2017, 13:04:09)

    Description:
    Process HMI magnetogram for running with SMART algorithm.
    Adapted from ar_processmag.pro originally written by P. Higgins

    Notes:
    -sunpy.wcs has been deprecated and needs to be replaced
    -there has to be a better way for median filtering a.l.a filer_image.pro

'''

import numpy as np
import scipy
from scipy import interpolate
import sunpy.wcs.wcs as wcs
from configparser import ConfigParser
import sunpy.map
import astropy.units as u

def ar_processmag(map):
    """Load input paramters, remove cosmic rays and NaNs,
    then make all off-limb pixels zero, and clean up limb,
    rotate map and do a cosine correction.
    """
    ## Load configuration file
    config = ConfigParser()
    config.read("config.ini")
    # Already floats in numpy data array so skipped first line converting double to float
    imgsz = len(map.data) #not 1024 x 1024 like idl
    # Didnt load parameters - need to add to config file
    # Didnt bother with indextag
    data = cosmicthresh_remove(map.data, np.float(config.get('processing', 'cosmicthresh')))
    # Clean NaNs - Higgo used bilinear interpoaltion
    data = remove_nans(data)
    # Clean edge - make all pixels off limb equal to 0. -- commented out as done during nan removal above!
    # map.data = edge_remove(map.data)
    # Rotate
    map = sunpy.map.Map(data, map.meta)
    map = map.rotate(angle=map.meta['crota2']*u.deg)
    # Cosine map
    cosmap, rrdeg, limbmask = ar_cosmap(map)
    # Remove limb issues
    data, limbmask = fix_limb(map.data, rrdeg, limbmask)
    # Median filter noisy values -- TO DO: do it properly like Higgo - check sunpy stuff - also commented as not used in main program
    # data = median_filter(data, np.float(config.get('processing', 'medfiltwidth')))
    map = sunpy.map.Map(data, map.meta)
    # Magnetic field cosine correction
    data, cosmap = cosine_correction(map, cosmap)
    map = sunpy.map.Map(data, map.meta)
    # Rotate solar norh = up -  dont get why Higgo is doing this so I'm going to ignore it!
    return map, cosmap, limbmask

def cosmicthresh_remove(data, cosmicthresh):
    """
    Search for cosmic rays using hard threshold defined in config file.
    Remove if greater than 3sigma detection than neighbooring pixels
    """
    wcosmic = np.where(data>cosmicthresh)
    ncosmic = len(wcosmic[0])
    print('Cosmic Ray Candidates Found: ', ncosmic)
    if (ncosmic == 0.):
        return data
    else:
        wcx = wcosmic[0]
        wcy = wcosmic[1]
        neighbours = np.int_([-1, 0, 1, 1, 1, 0, -1, -1])
        for i in range(0, ncosmic):
            wcx_neighbours = wcx[i] + neighbours
            wcy_neighbours = wcy[i] + neighbours
            wc_logic = (3*np.std(data[wcx_neighbours, wcy_neighbours]))+np.mean(data[wcx_neighbours, wcy_neighbours])
            if (data[wcx[i], wcy[i]] >= wc_logic):
                data[wcx[i], wcy[i]] = np.mean(data[wcx_neighbours, wcy_neighbours])
        return data

def remove_nans(array):
    """
    Clean NaNs
    Includes zero-value as 'missing'
    """
    x = np.arange(0, array.shape[1])
    y = np.arange(0, array.shape[0])
    # mask invalid values
    array = np.ma.masked_invalid(array)
    xx, yy = np.meshgrid(x, y)
    # get only the valid values
    x1 = xx[~array.mask]
    y1 = yy[~array.mask]
    newarr = array[~array.mask]
    GD1 = interpolate.griddata((x1, y1), newarr.ravel(),
                               (xx, yy),
                               method='cubic', fill_value=0.)
    return GD1

def edge_remove(data):
    """
    Get rid of crazy arbitrary values at the edge of the image
    Basically set everything beyond the limb equal to zero
    """
    edgepix = data[0, 0]
    wblankpx = np.where(data == edgepix)
    data[wblankpx] = 0.
    return data

def ar_cosmap(map):
    """
    Get the cosine map and off-limb pixel map using WCS.
    ;Generate a map of the solar disk that is 1 at disk center and goes radially outward as the cos(angle to LOS) 
    ;(= 2 at 60 degrees from LOS)
    ;optionally output:	rrdeg = gives degrees from disk center
    ;					wcs = wcs structure from input map file
    ;					offlimb = map of 1=on-disk and 0=off-disk
    """
    # take off an extra half percent from the disk to get rid of limb effects
    fudge=0.999
    #
    # get helioprojective_coordinates
    xx, yy = wcs.convert_pixel_to_data(map.data.shape,
                                                   [map.meta["CDELT1"], map.meta["CDELT2"]],
                                                   [map.meta["CRPIX1"], map.meta["CRPIX2"]],
                                                   [map.meta["CRVAL1"], map.meta["CRVAL2"]])
    rr = ((xx**2.) + (yy**2.))**(0.5)
    #
    coscor = rr
    rrdeg = np.arcsin(coscor / map.meta["RSUN_OBS"])
    coscor = 1. / np.cos(rrdeg)
    wgt = np.where(rr > (map.meta["RSUN_OBS"]*fudge))
    coscor[wgt] = 1.
    #
    offlimb = rr
    wgtrr = np.where(rr >= (map.meta["RSUN_OBS"]*fudge))
    offlimb[wgtrr] = 0.
    wltrr = np.where(rr < (map.meta["RSUN_OBS"]*fudge))
    offlimb[wltrr] = 1.
    #
    #below is from Sam's code
    # hcc_x, hcc_y, hcc_z = wcs.convert_hpc_hcc(x_coords, y_coords,
    #                                           dsun_meters=map.meta["DSUN_OBS"],
    #                                           z=True)
    # cosmap = cv2.normalize(hcc_z, None, 0, 1, cv2.NORM_MINMAX)
    return coscor, rrdeg, offlimb

def fix_limb(data, rrdeg, limbmask):
    """
    zero off-limb pixels
    zero from 80 degrees to LOS
    this is making the edge a bit smaller
    """
    maxlimb = 80.
    wofflimb = np.where(((rrdeg/(2.*np.pi))*360.) > maxlimb)
    data[wofflimb] = 0.
    limbmask[wofflimb] = 0.
    return data*limbmask, limbmask

def median_filter(data, medfiltwidth):
    """
    Median filter noisy values
    http://docs.sunpy.org/en/stable/generated/gallery/image_bright_regions_gallery_example.html
    """
    return scipy.ndimage.gaussian_filter(data, medfiltwidth)

def cosine_correction(map, cosmap):
    """
    Do magnetic field cosine correction
    Limit correction to having 1 pixel at edge of the Sun
    This is the maximum factor of pixel area covered by a single pixel at the solar limb as compared with at disk centre
    """
    thetalim = np.arcsin(1. - map.meta["CDELT1"] / map.meta["RSUN_OBS"])
    coscorlim = 1. / np.cos(thetalim)
    cosmaplim = np.where((cosmap) > coscorlim)
    cosmap[cosmaplim] = coscorlim
    return map.data*cosmap, cosmap


if __name__ == '__main__':
    ar_processmag()
