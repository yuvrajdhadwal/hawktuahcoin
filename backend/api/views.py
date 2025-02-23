# backend/api/views.py
import os
import praw
from dotenv import load_dotenv
import concurrent.futures
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from .services.coingecko import fetch_market_data  # Your existing fetch function
from .services.honeypot import check_honeypot
from .services.moralis import get_on_chain_info
from .services.reddit import fetch_subreddit_posts
from .services.sentiment import sentiment_desc
from .services.bot_detection import compute_bot_activity
from .models import RedditComment, RedditPost, CryptoTokenSentiment, CryptoTokenSpam

load_dotenv()

reddit = praw.Reddit(
    client_id= os.getenv('CLIENT_ID'),
    client_secret= os.getenv('CLIENT_SECRET'),
    username= os.getenv('REDDIT_USERNAME'),
    password= os.getenv('REDDIT_PASSWORD'),
    user_agent= os.getenv('REDDIT_APP')   
)

#############################################################################################
# Blockchain APIs
#############################################################################################

@api_view(['GET'])
def on_chain_info(request):
    """
    Example usage:
      GET /api/on_chain_info/?token=SRMuApVNdxXokk5GT7XD5cUUgXMBCoAz2LHeuAoKWRt&chain=mainnet

    'token' -> the Solana token address
    'chain' -> 'mainnet' or 'devnet'
    """
    # 1) Look for "token" in the query params, not the actual address key.
    network = request.GET.get("network", "mainnet")  # default to 'mainnet'
    token_address = request.GET.get("address", "SRMuApVNdxXokk5GT7XD5cUUgXMBCoAz2LHeuAoKWRt")


    # 2) Pass (network, token_address) in that order to your function
    data = get_on_chain_info(network, token_address)
    return Response(data)


@api_view(['GET'])
def market_data_view(request):
    """
    Example: /market-data/?tokens=ethereum,uniswap,bitcoin
    """
    # Read the 'tokens' query param; default to 'ethereum,bitcoin' if none provided
    tokens_str = request.GET.get('tokens', 'ethereum,bitcoin')
    # Split on commas to get a list
    tokens_list = tokens_str.split(',')
    
    # Now fetch data from CoinGecko for these tokens
    data = fetch_market_data(tokens_list)

    return Response(data)

@api_view(['GET'])
def honeypot_view(request):
    """
    Example: GET /api/honeypot/?token=0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48&chain=eth
    """
    token = request.GET.get("token", "")
    chain = request.GET.get("chain", "eth")
    
    if not token:
        return Response({"error": "Missing 'token' parameter"}, status=400)
    
    data = check_honeypot(token, chain)
    return Response(data)

#############################################################################################
# Reddit APIs
#############################################################################################

@api_view(["GET"])
def get_reddit_posts(request):
    """
    API endpoint to fetch Reddit posts with concurrent subreddit fetching.
    """
    query = request.GET.get("query", None)
    limit = int(request.GET.get("limit", 15))
    max_comments = int(request.GET.get("max_comments", 5))

    if not query:
        return Response({"error": "Crypto token required to query"}, status=status.HTTP_400_BAD_REQUEST)

    subreddit_list = [
        "cryptocurrency",
        "cryptomoonshots",
        "cryptoscams",
    ]

    reddit_query = f'"${query.lower()}" OR "{query.lower()} token" OR "{query.lower()} coin"'

    results = []

    # Use concurrent.futures to fetch multiple subreddits in parallel
    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = {executor.submit(fetch_subreddit_posts, subreddit, reddit_query, limit, max_comments, query, reddit): subreddit for subreddit in subreddit_list}

        for future in concurrent.futures.as_completed(futures):
            try:
                results.extend(future.result()) 
            except Exception as e:
                print(f"Error fetching subreddit {futures[future]}: {e}")

    return Response({"posts": results[:3]}, status=status.HTTP_200_OK)

@api_view(["GET"])
def get_stored_reddit_posts(request):
    """
    Retrieve stored Reddit posts filtered by token (query).
    Example request: /api/reddit/stored/?query=Bitcoin
    """
    token = request.GET.get("query", None)

    if not token:
        return Response({"error": "Query parameter is required."}, status=status.HTTP_400_BAD_REQUEST)

    posts = RedditPost.objects.filter(crypto_token=token).order_by("-created_at")

    if not posts.exists():
        return Response({"error": "No data found for this query."}, status=status.HTTP_404_NOT_FOUND)

    results = list(posts.values(
    "crypto_token", "subreddit", "title", "text", "post_author",
    "upvotes", "comments_count", "url"
    ))

    return Response({"posts": results}, status=status.HTTP_200_OK)

#############################################################################################
# Sentiment APIs
#############################################################################################

@api_view(["GET"])
def get_sentiment(request):
    token = request.GET.get("query", None)

    if not token:
        return Response({'Error': "Query Parameter is required."}, status=status.HTTP_400_BAD_REQUEST)
    
    try:
        sentiment =  CryptoTokenSentiment.objects.get(crypto_token=token)
    except CryptoTokenSentiment.DoesNotExist:
        create_sentiment(token)
        sentiment = CryptoTokenSentiment.objects.get(crypto_token=token)
        if not sentiment:
            return Response({'Error': "No sentiment data available."}, status=status.HTTP_404_NOT_FOUND)
    
    return Response({
        "crypto_token": sentiment.crypto_token,
        "overall_title_sentiment_value": sentiment.overall_title_sentiment_value,
        "overall_text_sentiment_value": sentiment.overall_text_sentiment_value,
        "overall_comment_sentiment_value": sentiment.overall_comment_sentiment_value,
        "overall_sentiment_value": sentiment.overall_sentiment_value,
        "overall_title_sentiment": sentiment.overall_title_sentiment,
        "overall_text_sentiment": sentiment.overall_text_sentiment,
        "overall_comment_sentiment": sentiment.overall_comment_sentiment,
        "overall_sentiment": sentiment.overall_sentiment,
    }, status=status.HTTP_200_OK)

def create_sentiment(token):
    '''creates sentiment of a certain crypto token'''
    
    posts = RedditPost.objects.filter(crypto_token=token).order_by("-created_at")

    if not posts.exists():        
        request = type('Request', (object,), {"GET": {"query": token, "limit": 15, "max_comments": 5}})
        response = get_reddit_posts(request)
        
        if response.status_code != 200 or not response.data.get("posts"):
            return None  # No posts found even after fetching

        # Re-fetch posts after updating
        posts = RedditPost.objects.filter(crypto_token=token).order_by("-created_at")

        if not posts.exists():
            return None  # Still no data, return failure
    
    analyzer = SentimentIntensityAnalyzer()

    title_sentiments = []
    text_sentiments = []
    comment_sentiments = []

    for post in posts:
        post_comments = RedditComment.objects.filter(post=post)

        title_sentiment = analyzer.polarity_scores(post.title)["compound"]
        text_sentiment = analyzer.polarity_scores(post.text)['compound']

        comment_sentiment_list = [analyzer.polarity_scores(comment.text)['compound'] for comment in post_comments]
        avg_comment_sentiment = sum(comment_sentiment_list) / len(comment_sentiment_list) if comment_sentiment_list else 0

        title_sentiments.append(title_sentiment)
        text_sentiments.append(text_sentiment)
        comment_sentiments.append(avg_comment_sentiment)

    overall_title_sentiment = sum(title_sentiments) / len(title_sentiments) if title_sentiments else 0
    overall_text_sentiment = sum(text_sentiments) / len(text_sentiments) if text_sentiments else 0
    overall_comment_sentiment = sum(comment_sentiments) / len(comment_sentiments) if comment_sentiments else 0

    overall_sentiment = (overall_title_sentiment * 0.3) + (overall_text_sentiment * 0.4) + (overall_comment_sentiment * 0.3)

    sentiment, created = CryptoTokenSentiment.objects.get_or_create(
            crypto_token=token,
            defaults={
                "crypto_token": token,
                "overall_title_sentiment_value": overall_title_sentiment,
                "overall_text_sentiment_value": overall_text_sentiment,
                "overall_comment_sentiment_value": overall_comment_sentiment,
                "overall_sentiment_value": overall_sentiment,
                "overall_title_sentiment": sentiment_desc(overall_title_sentiment),
                "overall_text_sentiment": sentiment_desc(overall_text_sentiment),
                "overall_comment_sentiment": sentiment_desc(overall_comment_sentiment),
                "overall_sentiment": sentiment_desc(overall_sentiment),
            }
        )
    
    return "it worked"


#############################################################################################
# Bot/Spam Detection APIs
#############################################################################################

@api_view(["GET"])
def bot_activity_view(request):
    token = request.GET.get('query', None)
    
    if not token:
        return Response({'Error': "Query parameter is required."}, status=status.HTTP_400_BAD_REQUEST)
    
    bot_activity = CryptoTokenSpam.objects.filter(crypto_token=token).order_by("-created_at").first()

    if not bot_activity: 
        posts = RedditPost.objects.filter(crypto_token=token)
        comments = RedditComment.objects.filter(post__in=posts)

        compute_bot_activity(posts, comments)

        bot_activity = CryptoTokenSpam.objects.filter(crypto_token=token).order_by("-created_at").first()

        if not bot_activity:
            return Response({'Error': "Failed to compute bot activity."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    return Response(
        {
            'Post Spam': bot_activity.post_spam,
            'Comment Spam': bot_activity.comment_spam
        },
        status=status.HTTP_200_OK
    )
