// Public Discogs Marketplace query/registry excerpt fetched 2026-10-03.
// Source: https://www.discogs.com/shop/list/assets/index-D3Jk_h7j.js
// This file is parsed, never executed.
const operation0 = gql`
  query MarketplaceSearch(
    $query: String
    $filter: MarketplaceSearchFilterInput
    $sort: MarketplaceSearchSortInput
    $first: Int!
    $after: String
  ) {
    marketplaceSearch(query: $query, filter: $filter, sort: $sort, first: $first, after: $after) {
      edges {
        cursor
        node {
          id
          isDeal
          offerOk
          inCart
          offerMade
          mediaCondition
          sleeveCondition
          publicNotes
          listedDate
          itemPhotos: images {
            position
            url
          }
          price {
            amount
            currency
          }
          buyerPrice {
            amount
            currency
          }
          shipping {
            price {
              amount
              currency
            }
            buyerPrice {
              amount
              currency
            }
            freeShippingMinimum {
              amount
              currency
            }
          }
          availability {
            isAvailable
            reason
          }
          seller {
            username
            discogsId
            avatarUrl
          }
          sellerInfo {
            rating
            ratingCount
            independentSeller
            shipsFrom
          }
          release {
            title
            discogsId
            released
            country
            # displayName is the label name as printed on this release; the union name is the
            # fallback. UnlinkedLabel has no discogsId, so those names render unlinked on the
            # card. labels includes every label-entity credit (series, companies, pressing
            # plants…); the mapper keeps only labelRole LABEL, matching the legacy
            # /sell/list "Label:" line.
            labels {
              displayName
              catalogNumber
              labelRole
              label {
                ... on Label {
                  discogsId
                  name
                }
                ... on UnlinkedLabel {
                  name
                }
              }
            }
            formatSummary {
              primaryFormat
              recordingTypes
              description
              issueType
            }
            primaryArtists {
              displayName
              artist {
                ... on Artist {
                  discogsId
                }
              }
            }
            images(first: 1) {
              edges {
                node {
                  nsfw
                  thumbnail {
                    webpUrl
                  }
                }
              }
            }
            inWantlist {
              discogsId
            }
            collectionItems(first: 1) {
              totalCount
            }
            myListings(first: 1) {
              totalCount
            }
          }
        }
      }
      pageInfo {
        hasNextPage
        endCursor
      }
      totalCount
      facetCounts {
        dimensionName
        facets {
          facetName
          totalCount
        }
      }
      bucketCounts {
        dimensionName
        buckets {
          lower
          upper
          totalCount
        }
      }
    }
    # Viewer identity, null when logged out. A listing whose seller.discogsId matches
    # viewer.discogsId renders the Edit/Unlist/Delete actions instead of the purchase buttons.
    # username links the inventory skittle to the viewer's own listings for the release.
    # wantlist.totalCount seeds the Seller filter's wantlist toggle default: ON at 1+ items, OFF
    # at 0. first: 0 fetches only the count, no edges. buyerCountry is the viewer's account
    # country, which takes precedence over the ESI ip_country for the "+ tax" note and the
    # unavailable-by-country label.
    viewer {
      discogsId
      username
      buyerCountry
      wantlist(first: 0) {
        totalCount
      }
    }
  }
`;

const operation1 = gql`
  query Styles {
    styles {
      discogsId
      name
      genre {
        discogsId
        name
      }
    }
  }
`;
const QUERY_HASHES = new Set([
  '7ce2bd7801f216b0999648d626c935fc86c8fd85e562529bd87b690c974f08c2',
  '4c514376b334ff2363ebf0c1b137de12d315ea16f6d492d20e46c89e7116843d'
]);
