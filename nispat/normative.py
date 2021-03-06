#!/Users/andre/sfw/anaconda3/bin/python

# ------------------------------------------------------------------------------
#  Usage:
#  python normative.py -m [maskfile] -k [number of CV folds] -c <covariates>
#                      -t [test covariates] -r [test responses] <infile>
#
#  Either the -k switch or -t switch should be specified, but not both.
#  If -t is selected, a set of responses should be provided with the -r switch
#
#  Written by A. Marquand
# ------------------------------------------------------------------------------

from __future__ import print_function
from __future__ import division

import os
import sys
import numpy as np
import argparse
import pickle
import glob

from sklearn.model_selection import KFold
try:  # run as a package if installed
    from nispat import fileio
    from nispat.normative_model.norm_utils import norm_init
    from nispat.utils import compute_pearsonr, CustomCV, explained_var, compute_MSLL
except ImportError:
    pass

    path = os.path.abspath(os.path.dirname(__file__))
    if path not in sys.path:
        sys.path.append(path)
        #sys.path.append(os.path.join(path,'normative_model'))
    del path

    import fileio
    from utils import compute_pearsonr, CustomCV, explained_var, compute_MSLL
    from normative_model.norm_utils import norm_init


def load_response_vars(datafile, maskfile=None, vol=True):
    """ load response variables (of any data type)"""

    if fileio.file_type(datafile) == 'nifti':
        dat = fileio.load_nifti(datafile, vol=vol)
        volmask = fileio.create_mask(dat, mask=maskfile)
        Y = fileio.vol2vec(dat, volmask).T
    else:
        Y = fileio.load(datafile)
        volmask = None
        if fileio.file_type(datafile) == 'cifti':
            Y = Y.T

    return Y, volmask


def get_args(*args):
    """ Parse command line arguments"""

    # parse arguments
    parser = argparse.ArgumentParser(description="Normative Modeling")
    parser.add_argument("responses")
    parser.add_argument("-m", help="mask file", dest="maskfile", default=None)
    parser.add_argument("-c", help="covariates file", dest="covfile",
                        default=None)
    parser.add_argument("-k", help="cross-validation folds", dest="cvfolds",
                        default=None)
    parser.add_argument("-t", help="covariates (test data)", dest="testcov",
                        default=None)
    parser.add_argument("-r", help="responses (test data)", dest="testresp",
                        default=None)
    parser.add_argument("-a", help="algorithm", dest="alg", default="gpr")
    parser.add_argument("-x", help="algorithm specific config options", 
                        dest="configparam", default=None)
    parser.add_argument('-s', action='store_false', 
                        help="Flag to skip standardization.", dest="standardize")
    parser.add_argument("keyword_args", nargs=argparse.REMAINDER)
    
    args = parser.parse_args()
    
    # Process required  arguemnts 
    wdir = os.path.realpath(os.path.curdir)
    respfile = os.path.join(wdir, args.responses)
    if args.covfile is None:
        raise(ValueError, "No covariates specified")
    else:
        covfile = args.covfile
    
    # Process optional arguments
    if args.maskfile is None:
        maskfile = None
    else:
        maskfile = os.path.join(wdir, args.maskfile)
    if args.testcov is None:
        testcov = None
        testresp = None
        cvfolds = int(args.cvfolds)
        print("Running under " + str(cvfolds) + " fold cross-validation.")
    else:
        print("Test covariates specified")
        testcov = args.testcov
        cvfolds = 1
        if args.testresp is None:
            testresp = None
            print("No test response variables specified")
        else:
            testresp = args.testresp
        if args.cvfolds is not None:
            print("Ignoring cross-valdation specification (test data given)")

    # Process addtional keyword arguments. These are always added as strings
    kw_args = {}
    for kw in args.keyword_args:
        kw_arg = kw.split('=')
    
        exec("kw_args.update({'" +  kw_arg[0] + "' : " + 
                              "'" + str(kw_arg[1]) + "'" + "})")
    
    return respfile, maskfile, covfile, cvfolds, \
            testcov, testresp, args.alg, \
            args.configparam, args.standardize, kw_args
            

def evaluate(Y, Yhat, S2=None, mY=None, sY=None,
             metrics = ['Rho', 'RMSE', 'SMSE', 'EXPV', 'MSLL']):
    
    feature_num = Y.shape[1]
    
    # find and remove bad variables from the response variables
    nz = np.where(np.bitwise_and(np.isfinite(Y).any(axis=0),
                                 np.var(Y, axis=0) != 0))[0]
    
    MSE = np.mean((Y - Yhat)**2, axis=0)
    
    results = dict()
    
    if 'RMSE' in metrics:
        RMSE = np.sqrt(MSE)
        results['RMSE'] = RMSE
    
    if 'Rho' in metrics:
        Rho = np.zeros(feature_num)
        pRho = np.ones(feature_num)    
        Rho[nz], pRho[nz] = compute_pearsonr(Y[:,nz], Yhat[:,nz])
        results['Rho'] = Rho
        results['pRho'] = pRho
        
    if 'SMSE' in metrics:
        SMSE = np.zeros_like(MSE)
        SMSE[nz] = MSE[nz] / np.var(Y[:,nz], axis=0)
        results['SMSE'] = SMSE
    
    if 'EXPV' in metrics:
        EXPV = np.zeros(feature_num)
        EXPV[nz] = explained_var(Y[:,nz], Yhat[:,nz])
        results['EXPV'] = EXPV
        
    if 'MSLL' in metrics:
        if ((S2 is not None) and (mY is not None) and (sY is not None)):
            MSLL = np.zeros(feature_num)
            MSLL[nz] = compute_MSLL(Y[:,nz], Yhat[:,nz], S2[:,nz], 
                                    mY[nz].reshape(-1,1).T, 
                                    (sY[nz]**2).reshape(-1,1).T)
            results['MSLL'] = MSLL
    
    return results

def save_results(respfile, Yhat, S2, maskvol, Z=None, outputsuffix=None, 
                 results=None, save_path=''):
    
    print("Writing output ...")
    if fileio.file_type(respfile) == 'cifti' or \
       fileio.file_type(respfile) == 'nifti':
        exfile = respfile
    else:
        exfile = None
    if outputsuffix is not None:
        ext = str(outputsuffix) + fileio.file_extension(respfile)
    else:
        ext = fileio.file_extension(respfile)
        
    fileio.save(Yhat, os.path.join(save_path, 'yhat' + ext), example=exfile, 
                                   mask=maskvol)
    fileio.save(S2, os.path.join(save_path, 'ys2' + ext), example=exfile, 
                mask=maskvol)
    if Z is not None:
        fileio.save(Z, os.path.join(save_path, 'Z' + ext), example=exfile, 
                    mask=maskvol)

    if results is not None:        
        for metric in list(results.keys()):
            fileio.save(results[metric], os.path.join(save_path, metric + ext), 
                        example=exfile, mask=maskvol)

def estimate(respfile, covfile, **kwargs):
    """ Estimate a normative model

    This will estimate a model in one of two settings according to the
    particular parameters specified (see below):

    * under k-fold cross-validation
        required settings 1) respfile 2) covfile 3) cvfolds>=2
    * estimating a training dataset then applying to a second test dataset
        required sessting 1) respfile 2) covfile 3) testcov 4) testresp
    * estimating on a training dataset ouput of forward maps mean and se
        required sessting 1) respfile 2) covfile 3) testcov

    The models are estimated on the basis of data stored on disk in ascii or
    neuroimaging data formats (nifti or cifti). Ascii data should be in
    tab or space delimited format with the number of subjects in rows and the
    number of variables in columns. Neuroimaging data will be reshaped
    into the appropriate format

    Basic usage::

        estimate(respfile, covfile, [extra_arguments])

    where the variables are defined below. Note that either the cfolds
    parameter or (testcov, testresp) should be specified, but not both.

    :param respfile: response variables for the normative model
    :param covfile: covariates used to predict the response variable
    :param maskfile: mask used to apply to the data (nifti only)
    :param cvfolds: Number of cross-validation folds
    :param testcov: Test covariates
    :param testresp: Test responses
    :param alg: Algorithm for normative model
    :param configparam: Parameters controlling the estimation algorithm
    :param saveoutput: Save the output to disk? Otherwise returned as arrays
    :param outputsuffix: Text string to add to the output filenames

    All outputs are written to disk in the same format as the input. These are:

    :outputs: * yhat - predictive mean
              * ys2 - predictive variance
              * nm - normative model
              * Z - deviance scores
              * Rho - Pearson correlation between true and predicted responses
              * pRho - parametric p-value for this correlation
              * rmse - root mean squared error between true/predicted responses
              * smse - standardised mean squared error

    The outputsuffix may be useful to estimate multiple normative models in the
    same directory (e.g. for custom cross-validation schemes)
    """
    
    # parse keyword arguments 
    maskfile = kwargs.pop('maskfile',None)
    cvfolds = kwargs.pop('cvfolds', None)
    testcov = kwargs.pop('testcov', None)
    testresp = kwargs.pop('testresp',None)
    alg = kwargs.pop('alg','gpr')
    saveoutput = kwargs.pop('saveoutput','True')=='True'
    savemodel = kwargs.pop('savemodel','False')=='True'
    outputsuffix = kwargs.pop('outputsuffix',None)
    standardize = kwargs.pop('standardize',True)
    
    if savemodel and not os.path.isdir('Models'):
        os.mkdir('Models')

    # load data
    print("Processing data in " + respfile)
    X = fileio.load(covfile)
    Y, maskvol = load_response_vars(respfile, maskfile)
    if len(Y.shape) == 1:
        Y = Y[:, np.newaxis]
    if len(X.shape) == 1:
        X = X[:, np.newaxis]
    Nmod = Y.shape[1]
    
    if testcov is not None: # we have a separate test dataset
        
        run_cv = False
        cvfolds = 1
        Xte = fileio.load(testcov)
        testids = range(X.shape[0], X.shape[0]+Xte.shape[0])
        if len(Xte.shape) == 1:
            Xte = Xte[:, np.newaxis]
        if testresp is not None:
            Yte, testmask = load_response_vars(testresp, maskfile)
            if len(Yte.shape) == 1:
                Yte = Yte[:, np.newaxis]
        else:
            sub_te = Xte.shape[0]
            Yte = np.zeros([sub_te, Nmod])
            
        # treat as a single train-test split
        splits = CustomCV((range(0, X.shape[0]),), (testids,))

        Y = np.concatenate((Y, Yte), axis=0)
        X = np.concatenate((X, Xte), axis=0)
        
    else:
        run_cv = True
        # we are running under cross-validation
        splits = KFold(n_splits=cvfolds)
        testids = range(0, X.shape[0])

    # find and remove bad variables from the response variables
    # note: the covariates are assumed to have already been checked
    nz = np.where(np.bitwise_and(np.isfinite(Y).any(axis=0),
                                 np.var(Y, axis=0) != 0))[0]

    # run cross-validation loop
    Yhat = np.zeros_like(Y)
    S2 = np.zeros_like(Y)
    Z = np.zeros_like(Y)
    nlZ = np.zeros((Nmod, cvfolds))
    
    mean_resp = []
    std_resp = []
    mean_cov = []
    std_cov = []

    for idx in enumerate(splits.split(X)):

        fold = idx[0]
        tr = idx[1][0]
        te = idx[1][1]

        # standardize responses and covariates, ignoring invalid entries
        iy, jy = np.ix_(tr, nz)
        mY = np.mean(Y[iy, jy], axis=0)
        sY = np.std(Y[iy, jy], axis=0)
        mean_resp.append(mY)
        std_resp.append(sY)
        if standardize:
            Yz = np.zeros_like(Y)
            Yz[:, nz] = (Y[:, nz] - mY) / sY
            mX = np.mean(X[tr, :], axis=0)
            sX = np.std(X[tr, :],  axis=0)
            Xz = (X - mX) / sX
            mean_resp.append(mY)
            std_resp.append(sY)
            mean_cov.append(mX)
            std_cov.append(sX)
        else:
            Yz = Y
            Xz = X

        # estimate the models for all subjects
        for i in range(0, len(nz)):  
            print("Estimating model ", i+1, "of", len(nz))
            nm = norm_init(Xz[tr, :], Yz[tr, nz[i]], alg=alg, **kwargs)
            try: 
                nm = nm.estimate(Xz[tr, :], Yz[tr, nz[i]])     
                
                yhat, s2 = nm.predict(Xz[te, :], Xz[tr, :], Yz[tr, nz[i]], **kwargs)
                
                if savemodel:
                    nm.save('Models/NM_' + str(fold) + '_' + str(nz[i]) + '.pkl' )
                
                if standardize:
                    Yhat[te, nz[i]] = yhat * sY[i] + mY[i]
                    S2[te, nz[i]] = s2 * sY[i]**2
                else:
                    Yhat[te, nz[i]] = yhat
                    S2[te, nz[i]] = s2
                    
                nlZ[nz[i], fold] = nm.neg_log_lik
                if (run_cv or testresp is not None):
                    Z[te, nz[i]] = (Y[te, nz[i]] - Yhat[te, nz[i]]) / \
                                   np.sqrt(S2[te, nz[i]])

            except Exception as e:
                exc_type, exc_obj, exc_tb = sys.exc_info()
                fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
                print("Model ", i+1, "of", len(nz),
                      "FAILED!..skipping and writing NaN to outputs")
                print("Exception:")
                print(e)
                print(exc_type, fname, exc_tb.tb_lineno)

                Yhat[te, nz[i]] = float('nan')
                S2[te, nz[i]] = float('nan')
                nlZ[nz[i], fold] = float('nan')
                if testcov is None:
                    Z[te, nz[i]] = float('nan')
                else:
                    if testresp is not None:
                        Z[te, nz[i]] = float('nan')
    if savemodel:
        print('Saving model meta-data...')
        with open('Models/meta_data.md', 'wb') as file:
            pickle.dump({'valid_voxels':nz, 'fold_num':cvfolds, 
                         'mean_resp':mean_resp, 'std_resp':std_resp, 
                         'mean_cov':mean_cov, 'std_cov':std_cov, 
                         'regressor':alg, 'standardize':standardize}, file)    

    # compute performance metrics
    if (run_cv or testresp is not None):
        print("Evaluating the model ...")
        results = evaluate(Y[testids, :], Yhat[testids, :], S2=S2[testids, :], 
                           mY=mean_resp[0], sY=std_resp[0])
        
    # Set writing options
    if saveoutput:
        print("Writing outputs ...")
        if (run_cv or testresp is not None):
            save_results(respfile, Yhat, S2, maskvol, Z=Z, results=results,
                         outputsuffix=outputsuffix)
            
        else:
            save_results(respfile, Yhat, S2, maskvol,outputsuffix=outputsuffix)
                
    else:
        if (run_cv or testresp is not None):
            output = (Yhat[testids, :], S2[testids, :], nm, Z[testids, :], results)
        else:
            output = (Yhat[testids, :], S2[testids, :], nm)
        
        return output

def predict(model_path, covfile, respfile=None, output_path=None,  
            maskfile=None, **kwargs):
    
    if not os.path.isdir(model_path):
        print('Model directory does not exist!')
        return
    else:
        with open(os.path.join(model_path, 'meta_data.md'), 'rb') as file:
            meta_data = pickle.load(file)
        cvfolds = meta_data['fold_num']
        standardize = meta_data['standardize']
        mY = meta_data['mean_resp']
        sY = meta_data['std_resp']
        mX = meta_data['mean_cov']
        sX = meta_data['std_cov']
    
    batch_effects_test = kwargs.pop('batch_effects_test', None)
    
    # load data
    print("Loading data ...")
    X = fileio.load(covfile)
    if len(X.shape) == 1:
        X = X[:, np.newaxis]
    
    sample_num = X.shape[0]
    feature_num = len(glob.glob(os.path.join(model_path, 'NM_*.pkl')))

    # run cross-validation loop
    Yhat = np.zeros([sample_num, feature_num])
    S2 = np.zeros([sample_num, feature_num])
    Z = np.zeros([sample_num, feature_num])
    
    for fold in range(cvfolds):
        
        if standardize:
            Xz = (X - mX[fold]) / sX[fold]
        else:
            Xz = X
            
        # estimate the models for all subjects
        for i in range(feature_num):
            print("Prediction by model ", i+1, "of", feature_num)      
            nm = norm_init(Xz)
            nm = nm.load(os.path.join(model_path, 'NM_' + str(fold) + '_' + str(i) + '.pkl'))
            if batch_effects_test is not None:
                nm.configs['batch_effects_test'] = batch_effects_test
            yhat, s2 = nm.predict(Xz)
            if standardize:
                Yhat[:, i] = yhat * sY[fold][i] + mY[fold][i]
                S2[:, i] = s2 * sY[fold][i]**2
            else:
                Yhat[:, i] = yhat
                S2[:, i] = s2
        
        if respfile is None:
            return (Yhat, S2)
        
        else:
            Y, maskvol = load_response_vars(respfile, maskfile)
            if len(Y.shape) == 1:
                Y = Y[:, np.newaxis]
            
            Z = (Y - Yhat) / np.sqrt(S2)
            
            print("Evaluating the model ...")
            results = evaluate(Y, Yhat, S2=S2, 
                               metrics = ['Rho', 'RMSE', 'SMSE', 'EXPV'])
            
            print("Evaluations Writing outputs ...")
            save_results(respfile, Yhat, S2, maskvol, Z=Z, outputsuffix=str(fold), 
                         results=results, save_path=output_path)
            
            return (Yhat, S2, Z)


def transfer(model_path, covfile, respfile, trbefile, output_path, testcov=None, 
             testresp=None, tsbefile=None, maskfile=None):
    
    if not os.path.isdir(output_path):
        os.mkdir(output_path)
    if not os.path.isdir(os.path.join(output_path,'Models')):
        transferred_models_path = os.path.join(output_path,'Models')
        os.mkdir(transferred_models_path)
    if not os.path.isdir(os.path.join(output_path,'Results')):
        results_path = os.path.join(output_path,'Results')
        os.mkdir(results_path)
    
    # load data
    print("Loading data ...")
    X = fileio.load(covfile)
    Y, maskvol = load_response_vars(respfile, maskfile)
    if len(Y.shape) == 1:
        Y = Y[:, np.newaxis]
    if len(X.shape) == 1:
        X = X[:, np.newaxis]
    feature_num = Y.shape[1]
    mY = np.mean(Y, axis=0)
    sY = np.std(Y, axis=0)
    
    if trbefile is not None:
        batch_effects_train = fileio.load(trbefile)
    else:
        batch_effects_train = np.zeros([X.shape[0],2])
    
    if testcov is not None:
        # we have a separate test dataset
        Xte = fileio.load(testcov)
        if len(Xte.shape) == 1:
            Xte = Xte[:, np.newaxis]
        ts_sample_num = Xte.shape[0]
        if testresp is not None:
            Yte, testmask = load_response_vars(testresp, maskfile)
            if len(Yte.shape) == 1:
                Yte = Yte[:, np.newaxis]
        else:
            Yte = np.zeros([ts_sample_num, feature_num])
        
        if tsbefile is not None:
            batch_effects_test = fileio.load(tsbefile)
        else:
            batch_effects_test = np.zeros([Xte.shape[0],2])

    Yhat = np.zeros([ts_sample_num, feature_num])
    S2 = np.zeros([ts_sample_num, feature_num])
    Z = np.zeros([ts_sample_num, feature_num])
    
    # estimate the models for all subjects
    for i in range(feature_num):
        print("Transferting model ", i+1, "of", feature_num)      
        nm = norm_init(X)
        nm = nm.load(os.path.join(model_path, 'NM_0_' + str(i) + '.pkl'))
        
        nm = nm.estimate_on_new_sites(X, Y[:,i], batch_effects_train)
        
        nm.save(os.path.join(transferred_models_path, 'NM_transfered_' + str(i) + '.pkl'))
        
        if testcov is not None:
            yhat, s2 = nm.predict_on_new_sites(Xte, batch_effects_test)
            Yhat[:, i] = yhat
            S2[:, i] = s2
   
    if testresp is None:
        save_results(respfile, Yhat, S2, maskvol, save_path=results_path)
        return (Yhat, S2)
    else:
        Z = (Yte - Yhat) / np.sqrt(S2)
    
        print("Evaluating the model ...")
        results = evaluate(Yte, Yhat, S2=S2, mY=mY, sY=sY)
                
        print("Writing outputs ...")
        save_results(respfile, Yhat, S2, maskvol, Z=Z, results=results, 
                     save_path=results_path)
        
        return (Yhat, S2, Z)

def main(*args):
    """ Parse arguments and estimate model
    """

    np.seterr(invalid='ignore')

    rfile, mfile, cfile, cv, tcfile, trfile, alg, cfg, std, kw = get_args(args)
    
    # collect required arguments
    pos_args = ['rfile', 'cfile']
    
    # collect basic keyword arguments controlling model estimation
    kw_args = ['maskfile=mfile',
               'cvfolds=cv',
               'testcov=tcfile',
               'testresp=trfile',
               'alg=alg',
               'configparam=cfg',
               'standardize=std']
    
    # add additional keyword arguments
    for k in kw:
        kw_args.append(k + '=' + "'" + kw[k] + "'")
    all_args = ', '.join(pos_args + kw_args)

    # estimate normative model
    exec('estimate(' + all_args + ')')
    #estimate(rfile, cfile, maskfile=mfile, cvfolds=cv,testcov=tcfile,
    #         testresp=trfile, alg=alg,configparam=cfg, saveoutput=True, 
    #         standardize=std)

# For running from the command line:
if __name__ == "__main__":
    main(sys.argv[1:])
