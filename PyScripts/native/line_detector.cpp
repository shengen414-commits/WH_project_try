#include <algorithm>
#include <array>
#include <cmath>
#include <iostream>

#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif

namespace {
constexpr int width=256, height=192, bands=18;
struct Point { double x,y; };
using Image=std::array<unsigned char,width*height>;

void detect(const Image& image, double roiTop, double maxWidth) {
    std::array<Point,bands> points{};
    int count=0, ambiguous=0;
    const int top=static_cast<int>(height*roiTop), bottom=height-10;
    for (int i=0;i<bands;++i) {
        const int y=top+2+i*(bottom-top-4)/(bands-1);
        std::array<int,width> profile{};
        std::array<int,256> histogram{};
        for(int x=8;x<width-8;++x) {
            profile[x]=(image[(y-1)*width+x]+image[y*width+x]+image[(y+1)*width+x])/3;
            ++histogram[profile[x]];
        }
        int median=0, accumulated=histogram[0];
        while(accumulated<(width-16)/2) accumulated+=histogram[++median];
        const int dark=median-24;
        int runs=0;
        Point candidate{};
        for(int x=8;x<width-8;) {
            if(profile[x]>dark) { ++x; continue; }
            const int start=x++;
            while(x<width-8 && profile[x]<=dark) ++x;
            const int span=x-start;
            if(span<3 || span>maxWidth*width || start<13 || x>width-13) continue;
            const int center=(start+x-1)/2;
            if(profile[start-4]-profile[center]<18 || profile[x+3]-profile[center]<18) continue;
            candidate={(start+x-1)*.5,static_cast<double>(y)};
            ++runs;
        }
        if (runs==1) points[count++]=candidate;
        else if (runs>1) ++ambiguous;
    }
    if (count<12 || ambiguous>2 || points[0].y>top+(bottom-top)*.35 ||
        points[count-1].y<bottom-(bottom-top)*.22) { std::cout<<"0\n"; return; }
    double meanX=0,meanY=0;
    for(int i=0;i<count;++i) {meanX+=points[i].x/count;meanY+=points[i].y/count;}
    double covariance=0,variance=0;
    for(int i=0;i<count;++i) {
        const double dy=points[i].y-meanY;
        covariance+=dy*(points[i].x-meanX);
        variance+=dy*dy;
    }
    const double slope=covariance/variance;
    double squaredError=0, peakError=0;
    for(int i=0;i<count;++i) {
        const double error=points[i].x-meanX-slope*(points[i].y-meanY);
        squaredError+=error*error;
        peakError=std::max(peakError,std::abs(error));
    }
    const double rms=std::sqrt(squaredError/count);
    if (rms>3.8 || peakError>8) { std::cout<<"0\n"; return; }
    const double confidence=std::min(1.,.55+.45*count/bands-.04*rms-.035*ambiguous);
    std::cout<<count<<' '<<confidence;
    for(int i=0;i<count;++i) std::cout<<' '<<points[i].x<<' '<<points[i].y;
    std::cout<<'\n';
}
}

int main() {
#ifdef _WIN32
    _setmode(_fileno(stdin),_O_BINARY);
#endif
    std::ios::sync_with_stdio(false);
    std::cin.tie(nullptr);
    Image image;
    double roiTop,maxWidth;
    // One request in flight. Each request is an ASCII settings line plus a
    // fixed-size grayscale frame, so no allocator or camera queue is needed.
    while (std::cin>>roiTop>>maxWidth) {
        if (!(roiTop>=.1 && roiTop<=.6 && maxWidth>=.03 && maxWidth<=.35) ||
            std::cin.get()!='\n') return 2;
        if (!std::cin.read(reinterpret_cast<char*>(image.data()),image.size())) return 2;
        detect(image,roiTop,maxWidth);
        std::cout.flush();
    }
}
