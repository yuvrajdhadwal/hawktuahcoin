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
from .services.sentiment import create_sentiment
from .models import RedditComment, RedditPost, CryptoTokenSentiment

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

    reddit_query = f"${query.lower()} OR {query.lower()} token OR {query.lower()} coin"

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

def get_sentiment(request):
    token = request.GET.get("query", None)

    if not token:
        return Response({'Error': "Query Parameter is required."}, status=status.HTTP_400_BAD_REQUEST)
    
    try:
        sentiment =  CryptoTokenSentiment.objects.get(token_address=token)
    except CryptoTokenSentiment.DoesNotExist:
        sentiment = create_sentiment(token)
        if not sentiment:
            return Response({'Error': "No sentiment data available."}, status=status.HTTP_404_NOT_FOUND)
    
    return Response({
        "crypto_token": sentiment.crypto_token,
        "overall_title_sentiment": sentiment.overall_title_sentiment,
        "overall_text_sentiment": sentiment.overall_text_sentiment,
        "overall_comment_sentiment": sentiment.overall_comment_sentiment,
        "overall_sentiment": sentiment.overall_sentiment
    }, status=status.HTTP_200_OK)
