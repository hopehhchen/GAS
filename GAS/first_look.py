import sys

from astropy.io import fits
from astropy import units as u
import numpy as np
import pyspeckit
from pyspeckit.parallel_map import parallel_map
from astropy import log
from spectral_cube import SpectralCube


def create_index( a, b):
    """ create_index takes two arrays and it creates an array that covers all indices 
    between a[i] and b[i].
    This function is useful to select the channels without emission for baseline fitting or 
    to identify the channels with the main emission.
    """
    return np.hstack(np.arange(start,stop+1) for start, stop in zip(a, b))

def blfunc_generator(x=None, polyorder=None, splineorder=None,
                     sampling=1):
    """
    Generate a function that will fit a baseline (polynomial or spline) to a
    data set.  Either ``splineorder`` or ``polyorder`` must be set
    Parameters
    ----------
    x : np.ndarray or None
        The X-axis of the fitted array.  Will be set to
        ``np.arange(len(data))`` if not specified
    polyorder : None or int
        The polynomial order.
    splineorder : None or int
    sampling : int
        The sampling rate to use for the data.  Can set to higher numbers to
        effectively downsample the data before fitting
    """
    def blfunc(args, x=x):
        yfit,yreal = args
        if hasattr(yfit,'mask'):
            mask = True-yfit.mask
        else:
            mask = np.isfinite(yfit)

        if x is None:
            x = np.arange(yfit.size, dtype=yfit.dtype)

        ngood = np.count_nonzero(mask)
        if polyorder is not None:
            if ngood <= polyorder:
                return yreal
            else:
                endpoint = ngood - (ngood % sampling)
                y = np.mean([yfit[mask][ii:endpoint:sampling]
                             for ii in range(sampling)], axis=0)
                polypars = np.polyfit(x[mask][sampling/2:endpoint:sampling],
                                      y, polyorder)
                return yreal-np.polyval(polypars, x).astype(yreal.dtype)

        elif splineorder is not None and scipyOK:
            if splineorder < 1 or splineorder > 4:
                raise ValueError("Spline order must be in {1,2,3,4}")
            elif ngood <= splineorder:
                return yreal
            else:
                log.debug("splinesampling: {0}  "
                          "splineorder: {1}".format(sampling, splineorder))
                endpoint = ngood - (ngood % sampling)
                y = np.mean([yfit[mask][ii:endpoint:sampling]
                             for ii in range(sampling)], axis=0)
                if len(y) <= splineorder:
                    raise ValueError("Sampling is too sparse.  Use finer sampling or "
                                     "decrease the spline order.")
                spl = UnivariateSpline(x[mask][sampling/2:endpoint:sampling],
                                       y,
                                       k=splineorder,
                                       s=0)
                return yreal-spl(x)
        else:
            raise ValueError("Must provide polyorder or splineorder")

    return blfunc

def baseline_cube(cube, polyorder=None, cubemask=None, splineorder=None,
                  numcores=None, sampling=1):
    """
    Given a cube, fit a polynomial to each spectrum
    Parameters
    Original version from pyspeckit. It should be included into "spectral-cube"
    ----------
    cube: np.ndarray
        An ndarray with ndim = 3, and the first dimension is the spectral axis
    polyorder: int
        Order of the polynomial to fit and subtract
    cubemask: boolean ndarray
        Mask to apply to cube.  Values that are True will be ignored when
        fitting.
    numcores : None or int
        Number of cores to use for parallelization.  If None, will be set to
        the number of available cores.
    """
    x = np.arange(cube.shape[0], dtype=cube.dtype)
    #polyfitfunc = lambda y: np.polyfit(x, y, polyorder)
    blfunc = blfunc_generator(x=x,
                              splineorder=splineorder,
                              polyorder=polyorder,
                              sampling=sampling)

    reshaped_cube = cube.reshape(cube.shape[0], cube.shape[1]*cube.shape[2]).T

    if cubemask is None:
        log.debug("No mask defined.")
        fit_cube = reshaped_cube
    else:
        if cubemask.dtype != 'bool':
            raise TypeError("Cube mask *must* be a boolean array.")
        if cubemask.shape != cube.shape:
            raise ValueError("Mask shape does not match cube shape")
        log.debug("Masking cube with shape {0} "
                  "with mask of shape {1}".format(cube.shape, cubemask.shape))
        masked_cube = cube.copy()
        masked_cube[cubemask] = np.nan
        fit_cube = masked_cube.reshape(cube.shape[0], cube.shape[1]*cube.shape[2]).T


    baselined = np.array(parallel_map(blfunc, zip(fit_cube,reshaped_cube), numcores=numcores))
    blcube = baselined.T.reshape(cube.shape)
    return blcube


def baseline( file_in, file_out, polyorder=1, index_clean=np.arange(0,100)):
    """  baseline: Function that reads in a cube and removes a baseline. 
    The baseline is a polynomial of order 'polyorder' (default=1), and it is fitted 
    on the channels clean of line emission, 'index_clean' (default=[0:100]).
    """
    # 
    cube, hd = fits.getdata(file_in, 0, header=True)
    # 
    # Create mask with data, then mask out channels without emission (index_clean)
    # and remove channels with NaNs
    cubemask = np.isfinite(cube)
    cubemask[index_clean,:,:] = False
    cubemask = (cubemask) | (np.isnan(cube))
    # Remove a line
    cube_bl = baseline_cube( cube, polyorder=polyorder, cubemask=cubemask, numcores=None, sampling=1)
    # Save cube
    hdu = fits.PrimaryHDU(cube_bl, header=hd)
    hdu.writeto(file_out, clobber=True)
    return file_out


def peak_rms( file_in, index_rms=np.arange(0,100), index_peak=np.arange(380,440), overwrite=True):
    """ Calculate rms, integrated intensity and peak intensity maps.

    Parameters
    ----------
    file_in : input FITS data cube. 

    index_rms  : array with channels to be used to Calculate rms.
    index_peak : array with channels to be used to peak intensity and integrated intensity map.

    overwrite : Boolean. If True (default) then the output FITS files overwrites any previous files.

    Returns
    -------
    It saves the rms, peak temperature and integrated intensity maps as FITS files.
    The fits files names are: 
        Mom0 : file_in.replace('.fits', '_mom0.fits')
        Tpeak: file_in.replace('.fits', '_mom0.fits')
        rms  : file_in.replace('.fits', '_Tpeak.fits')

    TODO
    ----
    If Bug in Spectral_Cube is fixed (https://github.com/radio-astro-tools/spectral-cube/pull/189), then 
    inds = np.arange(cube.shape[0])
    mask = (inds < 100) | (inds > 400)
    cube.with_mask(mask[:,None,None]).moment0()

    """
    #cube, hd = fits.getdata(file_in, 0, header=True)
    cube_raw = SpectralCube.read(file_in)
    cube = cube_raw.with_spectral_unit(u.km / u.s,velocity_convention='radio')
    # Creates masks for Main component and for channels of rms determination
    # This works but is not efficient!
    mask_mom=np.zeros( cube.shape, dtype=bool)
    mask_rms=np.zeros( cube.shape, dtype=bool)
    mask_mom[index_peak] = True
    mask_rms[index_rms]  = True
    mask_mom = mask_mom & np.isfinite( (cube.unmasked_data[:,:,:]).value )
    mask_rms = mask_rms & np.isfinite( (cube.unmasked_data[:,:,:]).value )
    cube_main = cube.with_mask(mask_mom)
    cube_rms  = cube.with_mask(mask_rms)
    #mask_mom=np.zeros( cube.shape[0], dtype=bool)
    #mask_rms=np.zeros( cube.shape[0], dtype=bool)
    #rms  =np.std( cube[index_rms, :,:], axis=0)
    #Tpeak=np.max( cube[index_peak,:,:], axis=0)
    mom_0 = cube_main.moment(order=0)
    mom_1 = cube_main.moment(order=1)
    rms   = cube_rms.std(axis=0)
    Tpeak = cube_main.max(axis=0)
    #
    ######beam11 = Beam.from_fits_header(fits.getheader(cube_raw))
    ######Beam.to_header_keywords()
    #
    #Tpeak = cube[index_peak].max(axis=0)
    #mom_0 = cube[index_peak].moment(order=0)
    #rms   = cube[index_rms].std(axis=0)
    print(file_in+'  Median rms='+ str(np.median(rms)))
    SNR=Tpeak.value/rms.value
    print(file_in+'  Median rms for SNR>5=' + str(np.median(rms[np.where(SNR>5)])))
    mom_0.write( file_in.replace('.fits', '_mom0.fits'), overwrite=overwrite)
    mom_1.write( file_in.replace('.fits', '_mom1.fits'), overwrite=overwrite)
    rms.write(   file_in.replace('.fits', '_rms.fits'), overwrite=overwrite)
    Tpeak.write( file_in.replace('.fits', '_Tpeak.fits'), overwrite=overwrite)
    ###SNR.write( file_in.replace('.fits', '_SNR.fits'), overwrite=True)

