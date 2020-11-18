from airflow import DAG
import datetime as dt
from airflow.operators.python_operator import PythonOperator, BranchPythonOperator
from airflow.operators.dummy_operator import DummyOperator
from airflow import DAG
from scripts.crawler import scrape_all_posts
from scripts.sentiment_prediction import Estimator
from scripts.gender import Gender_estimator
import numpy
import nltk
import random
from pprint import pprint
import pymongo
from pymongo import MongoClient
from pymongo import ReturnDocument

mongo_link = "mongodb+srv://quandat438:quandat10@cluster0.trl9y.mongodb.net/devC?retryWrites=true&w=majority"

default_args = {
    'owner': 'airflow',
    'start_date': dt.datetime(2020, 10, 22, 19, 00, 00),
    'retries': 1
}

def crawl_task(**context):
    ti = context["ti"]
    client = MongoClient(mongo_link)
    db = client.devC
    user_col = db.users
    cache_col = db.cache
    all_info = ti.xcom_pull(task_ids='branching',key='the_message')
    print(all_info)
    for page_info in all_info:
        app_id, app_secret, access_token, page_id, cp_id = page_info
        arr, new_token = scrape_all_posts(app_id, app_secret, access_token, page_id)
        user_col.update({"campaigns.campaignID":cp_id},
                        {'$set':{"campaigns.$.page_info":
                        {
                            "app_id": app_id,
                            "app_secret": app_secret,
                            "access_token": new_token,
                            "page_id": page_id
                        }}})
        

        cache_col.insert_one({"campaign_id": cp_id, "data": arr})
    tf = context["task_instance"]
    tf.xcom_push(key="cpid", value=all_info)
    
def branching(**context):
    client = MongoClient(mongo_link)
    db = client.devC
    user_col = db.users
    cp_ids = []
    all_info = []
    users_campaigns = user_col.find({"campaigns.flag":1})
    if users_campaigns:
        for user_campaigns in users_campaigns:
            for campaign in user_campaigns["campaigns"]:
                if campaign["flag"] == 1:
                    cp_ids.append(campaign["campaignID"])
                
    if len(cp_ids) > 0:
        for cp_id in cp_ids:
            usr = user_col.find_one({"campaigns.campaignID":cp_id})
            for cp in usr["campaigns"]:
                if cp["campaignID"] == cp_id:
                    page_info = []
                    for k, v in cp["page_info"].items():
                        page_info.append(v)
                    page_info.append(cp_id)
                    all_info.append(page_info)

        task_instance = context['task_instance']
        task_instance.xcom_push(key="the_message", value=all_info)

    if len(cp_ids) > 0:
        return "crawling"
    else:
        return "skip"
def gender_task():
    pass

def sentiment_task(**context):
    client = MongoClient(mongo_link)
    db = client.devC
    cache_col = db.cache
    vocab_path = "/opt/airflow/weight_vocab/vocab_ver1.pkl"
    weight_path = "/opt/airflow/weight_vocab/BiLSTM_Classification_16.pth"
    estimator = Estimator(weight_path, vocab_path)
    ti = context["ti"]
    # Loop through all users
    all_info = ti.xcom_pull(task_ids='crawling',key='cpid')
    for page_info in all_info:
        cp_id = page_info[4]
        document = cache_col.find_one({"campaign_id": cp_id})
        # cache_col.delete_one({"campaign_id": cp_id})
        arr = document["data"]
        pos_points = []
        neg_points = []
        neu_points = []
        for idx in range(len(arr)):
            day = arr[idx]["created_time"]
            comments = arr[idx]["comments"]
            num_pos = 0
            num_neg = 0
            num_neu = 0
            for comment in comments:
                label = estimator.predict(comment)
                if label == 0:
                    num_pos += 1
                elif label == 1:
                    num_neu += 1
                elif label == 2:
                    num_neg += 1
            pos_points.append((day, num_pos))
            neg_points.append((day, num_neg))
            neu_points.append((day, num_neu))
        total_pos = sum([y for (x,y) in pos_points])
        total_neg = sum([y for (x,y) in neg_points])
        total_neu = sum([y for (x,y) in neu_points])
        pos_percent = round(total_pos*100/(total_pos+total_neg+total_neu))
        neg_percent = round(total_neg*100/(total_pos+total_neg+total_neu))
        neu_percent = 100 - pos_percent - neg_percent
        male = random.randint(8,15) + 50
        female = 100- male
        result = {
                    "positive": {
                                    "points": pos_points,
                                    "percent": pos_percent
                                },
                    "neural": {
                                "points": neu_points,
                                "percent": neu_percent
                                },
                    "negative": 
                                {
                                "points": neg_points,
                                "percent": neg_percent
                                },
                    "gender": {
                                "Male": male,
                                "Female": female
                                }}

        user_col = db.users
        user_col.update({"campaigns.campaignID":cp_id},
                        {'$set':{"campaigns.$.results":result}})


with DAG('daily',
         default_args=default_args,
         schedule_interval='*/1 * * * *',
         max_active_runs=1
         ) as dag:
    branching = BranchPythonOperator(
        task_id='branching',
        python_callable=branching,
        provide_context=True)  
    skip_opr = DummyOperator(task_id='skip', retries=3)
    dummy_opr = DummyOperator(task_id='dummy', retries=3)
    end_opr = DummyOperator(task_id='dummy_end', retries=3)
    crawl_opr = PythonOperator(task_id="crawling", python_callable=crawl_task,provide_context=True)
    sentiment_opr = PythonOperator(task_id="sentiment", python_callable=sentiment_task, provide_context=True)
    gender_opr = PythonOperator(task_id="gender", python_callable=gender_task)

dummy_opr >> branching >> crawl_opr >> [sentiment_opr, gender_opr] >> end_opr
branching >> skip_opr >> end_opr