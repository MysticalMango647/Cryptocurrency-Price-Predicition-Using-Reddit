from operator import invert
from telnetlib import theNULL
import numpy as np
import pandas as pd
import json
import logging
from pathlib import Path
from matplotlib import pyplot as plt
from datetime import datetime
from statsmodels.tsa.vector_ar.var_model import VAR
from statsmodels.tsa.vector_ar.vecm import coint_johansen
from statsmodels.tsa.stattools import adfuller


logger = logging.getLogger("VAR")

REGRESSION_DAYS_USED = 60
NUM_OBS = 7
validate_or_predict = "predict"

def create_model(data):
    train = data[:-NUM_OBS]
    model = VAR(train)

    results = model.fit(3)
    return results

def invert_transformation(df_train, df_forecast, diff_used):
    """Revert back the differencing to get the forecast to original scale."""
    df_fc = df_forecast.copy()
    columns = df_train.columns
    for col in columns:        
        
        df_fc[str(col)+'_1d'] = (df_train[col].iloc[-1]-df_train[col].iloc[-2]) + df_fc[str(col)+'_2d'].cumsum()
        
        df_fc[str(col)+'_forecast'] = df_train[col].iloc[-1] + df_fc[str(col)+'_1d'].cumsum()

    return df_fc


def invert_transformation_2(df_train, df_forecast, n_diff = 2):
    """Revert back the differencing to get the forecast to original scale."""
    df_fc = df_forecast.copy()
    columns = df_train.columns

    for col in columns:   
        # roll back diff for each column
        for i in range(n_diff, 1):
            # Starting at N revert back to the nth diff
            nth_change = df_train[col].iloc[-1]

            if(n_diff >= 2):
                for n in range(2, i):
                    nth_change -= df_train[col].iloc[-i]
            
            df_fc[str(col) + "_{}d".format(n_diff)] = nth_change + df_fc[str(col) + "_{}d".format(n_diff)].cumsum()

        df_fc[str(col) + "_forecast"] = df_fc[str(col) + "_1d"]

    return df_fc

def forecast_model(model, input_data, original_data, diff_used = 2):
    lag_order = model.k_ar

    logger.info("LAG ORDER IS: {} ".format(lag_order))
    
    if validate_or_predict == "validate":
        forecasting_input = input_data.values[-(lag_order + NUM_OBS):-NUM_OBS]

        fc = model.forecast(forecasting_input, steps=NUM_OBS)

        idx = pd.date_range(str(original_data.index[-NUM_OBS]), periods=NUM_OBS, freq='D')
        forecast_data = pd.DataFrame(fc, index=idx, columns=input_data.columns + '_2d')
    else:   
        forecasting_input = input_data.values[-lag_order:]

        fc = model.forecast(forecasting_input, steps=NUM_OBS)

        idx = pd.date_range(str(original_data.index[-1]), periods=NUM_OBS, freq='D')
        forecast_data = pd.DataFrame(fc, index=idx, columns=input_data.columns + '_2d')

    return invert_transformation(original_data, forecast_data, diff_used)


def format_sentiment_and_coin_data(coin_data, sentiment_data, bitcoin_sentiment):
    coin_data = pd.DataFrame(coin_data)
    coin_specific_sentiment = sentiment_data.apply(pd.Series)[["count","sentiment"]].dropna()
    bitcoin_specific_sentiment = bitcoin_sentiment.apply(pd.Series)[["sentiment"]].dropna()

    bitcoin_specific_sentiment.columns = ['btc_sentiment']

    coin_data["timestamp"] = pd.to_datetime(coin_data["timestamp"]).dt.date

    coin_specific_sentiment.reset_index(inplace=True)
    coin_specific_sentiment.rename(columns = {"index": 'timestamp'}, inplace=True)
    coin_specific_sentiment["timestamp"] = pd.to_datetime(coin_specific_sentiment["timestamp"]).dt.date

    bitcoin_specific_sentiment.reset_index(inplace=True)
    bitcoin_specific_sentiment.rename(columns = {"index": 'timestamp'}, inplace=True)
    bitcoin_specific_sentiment["timestamp"] = pd.to_datetime(coin_specific_sentiment["timestamp"]).dt.date

    specific_sentiment = pd.merge(left = coin_specific_sentiment, right=bitcoin_specific_sentiment, how='left', left_on='timestamp', right_on='timestamp')

    merged_data = pd.merge(left=coin_data, right=specific_sentiment, how='left', left_on='timestamp', right_on='timestamp')
    merged_data["sentiment"].fillna(method="bfill", inplace=True)
    merged_data["btc_sentiment"].fillna(method="bfill", inplace=True)
    merged_data["count"].fillna(method="bfill", inplace=True)

    merged_data.index = pd.DatetimeIndex(merged_data["timestamp"]).to_period("d")
    merged_data.drop(labels = "timestamp", axis = 1, inplace=True)
    #merged_data.drop(labels = ["high", "low", "close", "volume", "marketCap", "count"], axis=1, inplace=True)
    merged_data.drop(labels = ["high", "low", "close", "volume", "marketCap"], axis=1, inplace=True)

    merged_data.dropna()

    return merged_data


if __name__ == "__main__":
    try:
        project_dir = Path(__file__).resolve().parents[2]

        log_fmt = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        logging.basicConfig(level=logging.INFO, format=log_fmt,
                            handlers= [
                                logging.FileHandler("{}/logs/reddit_data_logs.log".format(project_dir), mode = "w"),
                                logging.StreamHandler()
                            ])
                            
        coin_mapping_file = open("{}/data/raw/coinmarketcap/top_100_coins.json".format(project_dir))
        coin_mapping = json.load(coin_mapping_file)["coins"]

        new_coin_map = {}
        for coin_features in coin_mapping: 
            new_coin_map[coin_features["name"]] = coin_features


        coin_file = open("{}/data/raw/coinmarketcap/historical_data.json".format(project_dir))
        coin_data = json.load(coin_file)

        sentiment_data_df = pd.read_json("{}/data/processed/reddit_summary_all.json".format(project_dir), orient="index")
        keyword_sentiment_data = sentiment_data_df["keyword_based_sentiment"].apply(pd.Series)

        coin_forecasting = pd.DataFrame()

        for coin_name in coin_data:
            try:
                try:
                    coin_keyword_sentiment_data = keyword_sentiment_data[new_coin_map[str(coin_name)]["symbol"]]
                    bitcoin_sentiment_comparison = keyword_sentiment_data["BTC"]
                except Exception as e:
                    raise(Exception("Sentiment Data does not exist for this coin"))

                merged_data = format_sentiment_and_coin_data(coin_data[coin_name], coin_keyword_sentiment_data, bitcoin_sentiment_comparison)

                df_difference = merged_data.diff().diff().dropna()

                df_difference = df_difference[-REGRESSION_DAYS_USED:]

                model = create_model(df_difference)

                forecast_data = forecast_model(model, df_difference, merged_data[-REGRESSION_DAYS_USED:])

                actual_data = merged_data["open"]
                forecast_data = forecast_data["open_forecast"]
                
                if validate_or_predict == "validate":
                    forecast_data = forecast_data + (actual_data[-NUM_OBS] - forecast_data[0])
                    coin_forecasting[str(new_coin_map[str(coin_name)]["name"]).replace(" ", "_")] = forecast_data
                else:
                    forecast_data = forecast_data + (actual_data[-1] - forecast_data[0])
                    coin_forecasting[str(new_coin_map[str(coin_name)]["name"]).replace(" ", "_")] = forecast_data

                if coin_name == "Bitcoin" or coin_name == "Ethereum" or coin_name == "Polygon":
                    plt.figure(coin_name)
                    actual_data[-30:].plot(y="open")
                    forecast_data.plot(y="open_forecast")
                    plt.savefig("{}/src/models/forecasts/output_{}.jpg".format(project_dir, coin_name))
                
            except Exception as e:
                logger.error("An error has occured {}".format(e))

        # save the forecasting data for display 
        coin_forecasting.to_json("{}/data/processed/7d_forecast.json".format(project_dir), orient="index")


    except Exception as e:
        logger.error("An Error has occured {}".format(e))